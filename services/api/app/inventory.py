import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .db import get_connection
from .deps import require_admin
from .queue import publish_message
from .search_index import update_product_stock

router = APIRouter(prefix="/inventory", tags=["inventory"])

RESERVATION_TTL_MINUTES = 15


class InventoryAdjustRequest(BaseModel):
    product_id: str
    branch_id: str
    delta: int
    reason: str = Field(..., min_length=3, max_length=500)
    low_stock_threshold: Optional[int] = Field(None, ge=0)


class InventoryReserveRequest(BaseModel):
    product_id: str
    branch_id: str
    quantity: int = Field(..., gt=0)
    reservation_key: Optional[str] = Field(None, max_length=200)


class InventoryDeductRequest(BaseModel):
    product_id: str
    branch_id: str
    quantity: int = Field(..., gt=0)


class InventoryReleaseRequest(BaseModel):
    reservation_id: Optional[str] = None
    reservation_key: Optional[str] = None


class InventoryTransferRequest(BaseModel):
    product_id: str
    from_branch_id: str
    to_branch_id: str
    quantity: int = Field(..., gt=0)


class InventoryTransferStatusRequest(BaseModel):
    status: str = Field(..., min_length=3, max_length=20)


def _available(quantity: int, reserved: int) -> int:
    """Calculate available stock for a branch."""
    return quantity - reserved


def _ensure_inventory_row(conn, product_id: str, branch_id: str) -> None:
    """Create an inventory row if it does not exist."""
    conn.execute(
        """
        INSERT INTO inventory (product_id, branch_id)
        VALUES (%s, %s)
        ON CONFLICT (product_id, branch_id) DO NOTHING
        """,
        (product_id, branch_id),
    )


def _validate_product_and_branch(conn, product_id: str, branch_id: str) -> None:
    """Ensure product and branch exist before inventory mutations."""
    product = conn.execute(
        "SELECT id FROM products WHERE id = %s",
        (product_id,),
    ).fetchone()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    branch = conn.execute(
        "SELECT id FROM branches WHERE id = %s",
        (branch_id,),
    ).fetchone()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")


def _log_audit(
    conn,
    product_id: str,
    branch_id: str,
    actor_id: Optional[str],
    action: str,
    delta: int,
    reason: str,
) -> None:
    """Persist inventory audit log entries for manual changes."""
    conn.execute(
        """
        INSERT INTO inventory_audit_log (
          product_id,
          branch_id,
          actor_id,
          action,
          delta,
          reason
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (product_id, branch_id, actor_id, action, delta, reason),
    )


def _maybe_publish_low_stock(
    product_id: str,
    branch_id: str,
    previous_available: int,
    current_available: int,
    threshold: int,
) -> None:
    """Publish a low-stock event when availability crosses the threshold."""
    if previous_available >= threshold and current_available < threshold:
        payload = {
            "product_id": product_id,
            "branch_id": branch_id,
            "available": current_available,
            "threshold": threshold,
        }
        settings = get_settings()
        publish_message(settings.low_stock_queue_name, json.dumps(payload))


@router.get("/{product_id}")
def product_inventory(product_id: str):
    """Return stock levels across branches for a product."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
              i.product_id,
              i.branch_id,
              b.name,
              b.address,
              b.phone,
              b.is_active,
              i.quantity,
              i.reserved_qty,
              i.low_stock_threshold
            FROM inventory i
            JOIN branches b ON i.branch_id = b.id
            WHERE i.product_id = %s
            ORDER BY b.name ASC
            """,
            (product_id,),
        ).fetchall()

    return [
        {
            "product_id": str(row[0]),
            "branch_id": str(row[1]),
            "branch_name": row[2],
            "branch_address": row[3],
            "branch_phone": row[4],
            "branch_active": row[5],
            "quantity": row[6],
            "reserved_qty": row[7],
            "available_qty": _available(row[6], row[7]),
            "low_stock_threshold": row[8],
        }
        for row in rows
    ]


@router.get("/{product_id}/branch/{branch_id}")
def product_branch_inventory(product_id: str, branch_id: str):
    """Return stock levels for a product at a specific branch."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              i.product_id,
              i.branch_id,
              i.quantity,
              i.reserved_qty,
              i.low_stock_threshold,
              b.name,
              b.address,
              b.phone,
              b.is_active
            FROM inventory i
            JOIN branches b ON i.branch_id = b.id
            WHERE i.product_id = %s AND i.branch_id = %s
            """,
            (product_id, branch_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Inventory not found")

    return {
        "product_id": str(row[0]),
        "branch_id": str(row[1]),
        "quantity": row[2],
        "reserved_qty": row[3],
        "available_qty": _available(row[2], row[3]),
        "low_stock_threshold": row[4],
        "branch_name": row[5],
        "branch_address": row[6],
        "branch_phone": row[7],
        "branch_active": row[8],
    }


@router.post("/adjust")
def adjust_inventory(payload: InventoryAdjustRequest, user=Depends(require_admin)):
    """Adjust stock levels manually with audit logging."""
    if payload.delta == 0:
        raise HTTPException(status_code=400, detail="Delta cannot be zero")

    with get_connection() as conn:
        _validate_product_and_branch(conn, payload.product_id, payload.branch_id)
        _ensure_inventory_row(conn, payload.product_id, payload.branch_id)
        existing = conn.execute(
            """
            SELECT quantity, reserved_qty, low_stock_threshold
            FROM inventory
            WHERE product_id = %s AND branch_id = %s
            """,
            (payload.product_id, payload.branch_id),
        ).fetchone()

        if not existing and payload.delta < 0:
            raise HTTPException(status_code=400, detail="Insufficient stock")

        previous_available = (
            _available(existing[0], existing[1]) if existing else 0
        )
        updated = conn.execute(
            """
            UPDATE inventory
            SET
              quantity = quantity + %s,
              low_stock_threshold = COALESCE(%s, low_stock_threshold),
              updated_at = NOW()
            WHERE product_id = %s
              AND branch_id = %s
              AND (quantity + %s) >= reserved_qty
            RETURNING quantity, reserved_qty, low_stock_threshold
            """,
            (
                payload.delta,
                payload.low_stock_threshold,
                payload.product_id,
                payload.branch_id,
                payload.delta,
            ),
        ).fetchone()

        if not updated:
            raise HTTPException(status_code=409, detail="Insufficient stock")

        _log_audit(
            conn,
            payload.product_id,
            payload.branch_id,
            user.get("id"),
            "adjust",
            payload.delta,
            payload.reason,
        )

    current_available = _available(updated[0], updated[1])
    _maybe_publish_low_stock(
        payload.product_id,
        payload.branch_id,
        previous_available,
        current_available,
        updated[2],
    )
    update_product_stock(payload.product_id)
    return {
        "product_id": payload.product_id,
        "branch_id": payload.branch_id,
        "quantity": updated[0],
        "reserved_qty": updated[1],
        "available_qty": current_available,
        "low_stock_threshold": updated[2],
    }


@router.post("/reserve")
def reserve_inventory(payload: InventoryReserveRequest):
    """Soft-reserve stock for a cart with a TTL."""
    reservation_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=RESERVATION_TTL_MINUTES
    )

    with get_connection() as conn:
        _validate_product_and_branch(conn, payload.product_id, payload.branch_id)
        _ensure_inventory_row(conn, payload.product_id, payload.branch_id)
        existing = conn.execute(
            """
            SELECT quantity, reserved_qty, low_stock_threshold
            FROM inventory
            WHERE product_id = %s AND branch_id = %s
            """,
            (payload.product_id, payload.branch_id),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Inventory not found")

        previous_available = _available(existing[0], existing[1])
        updated = conn.execute(
            """
            UPDATE inventory
            SET reserved_qty = reserved_qty + %s, updated_at = NOW()
            WHERE product_id = %s
              AND branch_id = %s
              AND (quantity - reserved_qty) >= %s
            RETURNING quantity, reserved_qty, low_stock_threshold
            """,
            (
                payload.quantity,
                payload.product_id,
                payload.branch_id,
                payload.quantity,
            ),
        ).fetchone()

        if not updated:
            raise HTTPException(status_code=409, detail="Insufficient stock")

        conn.execute(
            """
            INSERT INTO inventory_reservations (
              id,
              product_id,
              branch_id,
              quantity,
              reservation_key,
              expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                reservation_id,
                payload.product_id,
                payload.branch_id,
                payload.quantity,
                payload.reservation_key,
                expires_at,
            ),
        )

        _log_audit(
            conn,
            payload.product_id,
            payload.branch_id,
            None,
            "reserve",
            payload.quantity,
            "cart_reserve",
        )

    current_available = _available(updated[0], updated[1])
    _maybe_publish_low_stock(
        payload.product_id,
        payload.branch_id,
        previous_available,
        current_available,
        updated[2],
    )
    update_product_stock(payload.product_id)
    return {
        "reservation_id": reservation_id,
        "expires_at": expires_at,
        "available_qty": current_available,
    }


@router.post("/deduct")
def deduct_inventory(payload: InventoryDeductRequest, user=Depends(require_admin)):
    """Deduct reserved stock atomically on order placement."""
    with get_connection() as conn:
        _validate_product_and_branch(conn, payload.product_id, payload.branch_id)
        source = conn.execute(
            """
            SELECT quantity, reserved_qty, low_stock_threshold
            FROM inventory
            WHERE product_id = %s AND branch_id = %s
            """,
            (payload.product_id, payload.branch_id),
        ).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Inventory not found")

        previous_available = _available(source[0], source[1])
        updated = conn.execute(
            """
            UPDATE inventory
            SET
              quantity = quantity - %s,
              reserved_qty = reserved_qty - %s,
              updated_at = NOW()
            WHERE product_id = %s
              AND branch_id = %s
              AND quantity >= %s
              AND reserved_qty >= %s
            RETURNING quantity, reserved_qty, low_stock_threshold
            """,
            (
                payload.quantity,
                payload.quantity,
                payload.product_id,
                payload.branch_id,
                payload.quantity,
                payload.quantity,
            ),
        ).fetchone()

        if not updated:
            raise HTTPException(status_code=409, detail="Insufficient stock")

        _log_audit(
            conn,
            payload.product_id,
            payload.branch_id,
            user.get("id"),
            "deduct",
            -payload.quantity,
            "order_deduction",
        )

    current_available = _available(updated[0], updated[1])
    _maybe_publish_low_stock(
        payload.product_id,
        payload.branch_id,
        previous_available,
        current_available,
        updated[2],
    )
    update_product_stock(payload.product_id)
    return {
        "product_id": payload.product_id,
        "branch_id": payload.branch_id,
        "quantity": updated[0],
        "reserved_qty": updated[1],
        "available_qty": current_available,
    }


@router.post("/release")
def release_inventory(payload: InventoryReleaseRequest):
    """Release reserved stock by reservation id or key."""
    if not payload.reservation_id and not payload.reservation_key:
        raise HTTPException(
            status_code=400,
            detail="reservation_id or reservation_key is required",
        )

    with get_connection() as conn:
        if payload.reservation_id:
            rows = conn.execute(
                """
                SELECT id, product_id, branch_id, quantity
                FROM inventory_reservations
                WHERE id = %s
                """,
                (payload.reservation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, product_id, branch_id, quantity
                FROM inventory_reservations
                WHERE reservation_key = %s
                """,
                (payload.reservation_key,),
            ).fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="Reservation not found")

        grouped: dict[tuple[str, str], int] = {}
        for row in rows:
            key = (str(row[1]), str(row[2]))
            grouped[key] = grouped.get(key, 0) + row[3]

        with conn.transaction():
            for (product_id, branch_id), quantity in grouped.items():
                conn.execute(
                    """
                    UPDATE inventory
                    SET reserved_qty = GREATEST(reserved_qty - %s, 0),
                        updated_at = NOW()
                    WHERE product_id = %s AND branch_id = %s
                    """,
                    (quantity, product_id, branch_id),
                )
                _log_audit(
                    conn,
                    product_id,
                    branch_id,
                    None,
                    "release",
                    -quantity,
                    "reservation_release",
                )

            ids = [row[0] for row in rows]
            conn.execute(
                "DELETE FROM inventory_reservations WHERE id = ANY(%s)",
                (ids,),
            )

    for product_id, _branch_id in grouped.keys():
        update_product_stock(product_id)

    return {"released": len(rows)}


@router.get("/low-stock")
def low_stock(
    branch_id: Optional[str] = None,
    user=Depends(require_admin),
):
    """List products below their low stock threshold."""
    conditions = ["(i.quantity - i.reserved_qty) < i.low_stock_threshold"]
    params: list = []
    if branch_id:
        conditions.append("i.branch_id = %s")
        params.append(branch_id)

    where_clause = " AND ".join(conditions)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
              i.product_id,
              p.name,
              p.slug,
              i.branch_id,
              b.name,
              b.phone,
              i.quantity,
              i.reserved_qty,
              i.low_stock_threshold
            FROM inventory i
            JOIN products p ON i.product_id = p.id
            JOIN branches b ON i.branch_id = b.id
            WHERE {where_clause}
            ORDER BY (i.quantity - i.reserved_qty) ASC
            """,
            params,
        ).fetchall()

    return [
        {
            "product_id": str(row[0]),
            "product_name": row[1],
            "product_slug": row[2],
            "branch_id": str(row[3]),
            "branch_name": row[4],
            "branch_phone": row[5],
            "quantity": row[6],
            "reserved_qty": row[7],
            "available_qty": _available(row[6], row[7]),
            "low_stock_threshold": row[8],
        }
        for row in rows
    ]


@router.post("/transfer")
def create_transfer(
    payload: InventoryTransferRequest, user=Depends(require_admin)
):
    """Request a stock transfer between branches."""
    if payload.from_branch_id == payload.to_branch_id:
        raise HTTPException(
            status_code=400, detail="Source and destination must differ"
        )

    with get_connection() as conn:
        _validate_product_and_branch(
            conn, payload.product_id, payload.from_branch_id
        )
        _validate_product_and_branch(
            conn, payload.product_id, payload.to_branch_id
        )
        _ensure_inventory_row(conn, payload.product_id, payload.from_branch_id)
        _ensure_inventory_row(conn, payload.product_id, payload.to_branch_id)

        row = conn.execute(
            """
            INSERT INTO inventory_transfers (
              product_id,
              from_branch_id,
              to_branch_id,
              quantity,
              status,
              requested_by
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, status
            """,
            (
                payload.product_id,
                payload.from_branch_id,
                payload.to_branch_id,
                payload.quantity,
                "pending",
                user.get("id"),
            ),
        ).fetchone()

    return {"transfer_id": str(row[0]), "status": row[1]}


@router.get("/transfers")
def list_transfers(
    branch_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 24,
    user=Depends(require_admin),
):
    """List inventory transfers with optional filters."""
    offset = max(page - 1, 0) * per_page
    conditions = []
    params: list = []
    if branch_id:
        conditions.append(
            "(from_branch_id = %s OR to_branch_id = %s)"
        )
        params.extend([branch_id, branch_id])
    if status:
        conditions.append("status = %s")
        params.append(status)

    where_clause = " AND ".join(conditions)
    where_sql = f"WHERE {where_clause}" if where_clause else ""
    params.extend([per_page, offset])

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
              id,
              product_id,
              from_branch_id,
              to_branch_id,
              quantity,
              status,
              created_at,
              updated_at
            FROM inventory_transfers
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "product_id": str(row[1]),
            "from_branch_id": str(row[2]),
            "to_branch_id": str(row[3]),
            "quantity": row[4],
            "status": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }
        for row in rows
    ]


@router.patch("/transfers/{transfer_id}/status")
def update_transfer_status(
    transfer_id: str,
    payload: InventoryTransferStatusRequest,
    user=Depends(require_admin),
):
    """Update transfer status and apply stock movement on completion."""
    transitions = {
        "pending": "approved",
        "approved": "in_transit",
        "in_transit": "completed",
    }
    new_status = payload.status

    with get_connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """
                SELECT
                  id,
                  product_id,
                  from_branch_id,
                  to_branch_id,
                  quantity,
                  status
                FROM inventory_transfers
                WHERE id = %s
                FOR UPDATE
                """,
                (transfer_id,),
            ).fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Transfer not found")

            current_status = row[5]
            expected_next = transitions.get(current_status)
            if expected_next != new_status:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid transfer status transition",
                )

            product_id = str(row[1])
            from_branch_id = str(row[2])
            to_branch_id = str(row[3])
            quantity = row[4]

            if new_status == "approved":
                conn.execute(
                    """
                    UPDATE inventory_transfers
                    SET status = %s,
                        approved_by = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_status, user.get("id"), transfer_id),
                )
            elif new_status == "in_transit":
                conn.execute(
                    """
                    UPDATE inventory_transfers
                    SET status = %s,
                        in_transit_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_status, transfer_id),
                )
            elif new_status == "completed":
                source = conn.execute(
                    """
                    SELECT quantity, reserved_qty, low_stock_threshold
                    FROM inventory
                    WHERE product_id = %s AND branch_id = %s
                    """,
                    (product_id, from_branch_id),
                ).fetchone()
                if not source:
                    raise HTTPException(
                        status_code=404, detail="Source inventory missing"
                    )
                previous_available = _available(source[0], source[1])
                updated_source = conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = quantity - %s, updated_at = NOW()
                    WHERE product_id = %s
                      AND branch_id = %s
                      AND (quantity - reserved_qty) >= %s
                    RETURNING quantity, reserved_qty, low_stock_threshold
                    """,
                    (quantity, product_id, from_branch_id, quantity),
                ).fetchone()
                if not updated_source:
                    raise HTTPException(
                        status_code=409, detail="Insufficient stock for transfer"
                    )

                _ensure_inventory_row(conn, product_id, to_branch_id)
                conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = quantity + %s, updated_at = NOW()
                    WHERE product_id = %s AND branch_id = %s
                    """,
                    (quantity, product_id, to_branch_id),
                )

                _log_audit(
                    conn,
                    product_id,
                    from_branch_id,
                    user.get("id"),
                    "transfer_out",
                    -quantity,
                    f"transfer:{transfer_id}",
                )
                _log_audit(
                    conn,
                    product_id,
                    to_branch_id,
                    user.get("id"),
                    "transfer_in",
                    quantity,
                    f"transfer:{transfer_id}",
                )

                conn.execute(
                    """
                    UPDATE inventory_transfers
                    SET status = %s,
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_status, transfer_id),
                )

                current_available = _available(
                    updated_source[0], updated_source[1]
                )
                _maybe_publish_low_stock(
                    product_id,
                    from_branch_id,
                    previous_available,
                    current_available,
                    updated_source[2],
                )

    update_product_stock(product_id)
    return {"id": transfer_id, "status": new_status}
