import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .db import get_connection
from .deps import get_current_user, require_admin
from .queue import publish_message

router = APIRouter(prefix="/orders", tags=["returns"])
admin_router = APIRouter(prefix="/admin/returns", tags=["returns"])
warranty_router = APIRouter(prefix="/warranty-claims", tags=["warranty"])
admin_warranty_router = APIRouter(
    prefix="/admin/warranty-claims", tags=["warranty"]
)

RETURN_WINDOW_DAYS = 7
ALLOWED_RETURN_STATUSES = {"approved", "rejected", "completed"}


class ReturnItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)


class ReturnCreateRequest(BaseModel):
    items: list[ReturnItemRequest]
    reason: Optional[str] = Field(None, max_length=1000)


class ReturnStatusUpdateRequest(BaseModel):
    status: str = Field(..., min_length=3, max_length=30)
    note: Optional[str] = Field(None, max_length=500)


class WarrantyClaimRequest(BaseModel):
    order_id: str
    product_id: str
    issue_desc: str = Field(..., min_length=5, max_length=2000)
    photos: Optional[list[str]] = None


def _get_delivered_at(conn, order_id: str) -> Optional[datetime]:
    """Fetch the delivery timestamp for an order."""
    row = conn.execute(
        """
        SELECT changed_at
        FROM order_status_history
        WHERE order_id = %s AND status = 'delivered'
        ORDER BY changed_at DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    return row[0] if row else None


def _validate_return_window(delivered_at: Optional[datetime]) -> None:
    """Ensure return window is still valid."""
    if not delivered_at:
        raise HTTPException(status_code=422, detail="Order not delivered")
    if datetime.now(timezone.utc) > delivered_at + timedelta(
        days=RETURN_WINDOW_DAYS
    ):
        raise HTTPException(
            status_code=422, detail="Return window closed"
        )


@router.post("/{order_id}/return")
def create_return_request(
    order_id: str,
    payload: ReturnCreateRequest,
    user=Depends(get_current_user),
):
    """Create a return request for an order."""
    with get_connection() as conn:
        order = conn.execute(
            """
            SELECT id
            FROM orders
            WHERE id = %s AND user_id = %s
            """,
            (order_id, user["id"]),
        ).fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        delivered_at = _get_delivered_at(conn, order_id)
        _validate_return_window(delivered_at)

        order_items = conn.execute(
            """
            SELECT product_id, quantity
            FROM order_items
            WHERE order_id = %s
            """,
            (order_id,),
        ).fetchall()
        qty_map = {str(row[0]): row[1] for row in order_items}

        for item in payload.items:
            if item.product_id not in qty_map:
                raise HTTPException(
                    status_code=404, detail="Item not in order"
                )
            if item.quantity > qty_map[item.product_id]:
                raise HTTPException(
                    status_code=422, detail="Invalid return quantity"
                )

        row = conn.execute(
            """
            INSERT INTO return_requests (
              order_id,
              user_id,
              items,
              reason,
              status
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                order_id,
                user["id"],
                json.dumps([item.dict() for item in payload.items]),
                payload.reason,
                "pending",
            ),
        ).fetchone()

    return {"return_id": str(row[0])}


@router.get("/returns")
def list_returns(user=Depends(get_current_user)):
    """List return requests for the authenticated user."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, order_id, status, created_at
            FROM return_requests
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user["id"],),
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "order_id": str(row[1]),
            "status": row[2],
            "created_at": row[3].isoformat(),
        }
        for row in rows
    ]


@admin_router.get("")
def admin_list_returns(
    status: Optional[str] = None, user=Depends(require_admin)
):
    """List all return requests for admins."""
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                """
                SELECT id, order_id, user_id, status, created_at
                FROM return_requests
                WHERE status = %s
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, order_id, user_id, status, created_at
                FROM return_requests
                ORDER BY created_at DESC
                """
            ).fetchall()

    return [
        {
            "id": str(row[0]),
            "order_id": str(row[1]),
            "user_id": str(row[2]),
            "status": row[3],
            "created_at": row[4].isoformat(),
        }
        for row in rows
    ]


@admin_router.patch("/{return_id}/status")
def update_return_status(
    return_id: str,
    payload: ReturnStatusUpdateRequest,
    user=Depends(require_admin),
):
    """Update return request status and trigger refunds if approved."""
    if payload.status not in ALLOWED_RETURN_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid return status")

    settings = get_settings()
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE return_requests
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING order_id
            """,
            (payload.status, return_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Return not found")

    if payload.status == "approved":
        publish_message(
            settings.refund_queue_name,
            json.dumps(
                {
                    "return_id": return_id,
                    "order_id": str(row[0]),
                    "reason": "return_approved",
                }
            ),
        )

    return {"status": payload.status}


@warranty_router.post("")
def create_warranty_claim(
    payload: WarrantyClaimRequest, user=Depends(get_current_user)
):
    """Submit a warranty claim for a purchased product."""
    with get_connection() as conn:
        order = conn.execute(
            """
            SELECT id
            FROM orders
            WHERE id = %s AND user_id = %s
            """,
            (payload.order_id, user["id"]),
        ).fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        order_item = conn.execute(
            """
            SELECT 1
            FROM order_items
            WHERE order_id = %s AND product_id = %s
            """,
            (payload.order_id, payload.product_id),
        ).fetchone()
        if not order_item:
            raise HTTPException(
                status_code=404, detail="Product not in order"
            )

        row = conn.execute(
            """
            INSERT INTO warranty_claims (
              order_id,
              product_id,
              user_id,
              issue_desc,
              photos,
              status
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                payload.order_id,
                payload.product_id,
                user["id"],
                payload.issue_desc,
                json.dumps(payload.photos) if payload.photos else None,
                "pending",
            ),
        ).fetchone()

    return {"claim_id": str(row[0])}


@admin_warranty_router.get("")
def list_warranty_claims(user=Depends(require_admin)):
    """List warranty claims for administrators."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, order_id, product_id, user_id, status, created_at
            FROM warranty_claims
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "order_id": str(row[1]),
            "product_id": str(row[2]),
            "user_id": str(row[3]),
            "status": row[4],
            "created_at": row[5].isoformat(),
        }
        for row in rows
    ]
