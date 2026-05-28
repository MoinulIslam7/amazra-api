import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .config import get_settings
from .coupons import calculate_discount, validate_coupon_for_cart
from .db import get_connection
from .deps import get_current_user, require_admin
from .queue import publish_message
from .search_index import update_product_stock

router = APIRouter(prefix="/orders", tags=["orders"])
admin_router = APIRouter(prefix="/admin/orders", tags=["orders"])

ALLOWED_TRANSITIONS = {
    "placed": {"confirmed", "cancelled"},
    "confirmed": {"packed"},
    "packed": {"shipped"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
    "returned": set(),
}


class OrderCreateRequest(BaseModel):
    address_id: str
    payment_method: str = Field(..., min_length=3, max_length=30)
    payment_ref: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=2000)


class OrderStatusUpdateRequest(BaseModel):
    status: str = Field(..., min_length=3, max_length=30)
    note: Optional[str] = Field(None, max_length=500)


def _next_order_reference(conn) -> str:
    """Generate a sequential order reference."""
    seq = conn.execute("SELECT nextval('order_reference_seq')").fetchone()[0]
    year = datetime.now(timezone.utc).year
    return f"ST-{year}-{seq:05d}"


def _load_cart(conn, user_id: str) -> tuple[str, Optional[str]]:
    """Load the cart id and applied coupon for a user."""
    row = conn.execute(
        "SELECT id, applied_coupon_id FROM carts WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Cart is empty")
    return str(row[0]), str(row[1]) if row[1] else None


def _load_cart_items(conn, cart_id: str) -> list[dict]:
    """Load cart items with product pricing."""
    rows = conn.execute(
        """
        SELECT
          ci.product_id,
          ci.branch_id,
          ci.quantity,
          p.name,
          p.price,
          p.original_price
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.cart_id = %s
        ORDER BY ci.created_at ASC
        """,
        (cart_id,),
    ).fetchall()
    items = []
    for row in rows:
        line_total = row[4] * row[2]
        items.append(
            {
                "product_id": str(row[0]),
                "branch_id": str(row[1]),
                "quantity": row[2],
                "product_name": row[3],
                "unit_price": row[4],
                "original_price": row[5],
                "line_total": line_total,
            }
        )
    return items


def _ensure_single_branch(items: list[dict]) -> str:
    """Ensure all cart items belong to a single branch."""
    branch_ids = {item["branch_id"] for item in items}
    if len(branch_ids) != 1:
        raise HTTPException(
            status_code=422,
            detail="Cart items must belong to a single branch",
        )
    return next(iter(branch_ids))


def _load_address_snapshot(conn, address_id: str, user_id: str) -> dict:
    """Load an address and return it as a JSON snapshot."""
    row = conn.execute(
        """
        SELECT name, phone, line1, line2, district, division, postcode
        FROM addresses
        WHERE id = %s AND user_id = %s
        """,
        (address_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Address not found")
    return {
        "name": row[0],
        "phone": row[1],
        "line1": row[2],
        "line2": row[3],
        "district": row[4],
        "division": row[5],
        "postcode": row[6],
    }


def _build_invoice_pdf(order: dict, items: list[dict]) -> bytes:
    """Render a simple PDF invoice for an order."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(40, 750, "Amazra Invoice")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(40, 730, f"Order: {order['reference']}")
    pdf.drawString(40, 715, f"Date: {order['created_at']}")
    pdf.drawString(40, 700, f"Customer: {order['customer_name']}")

    y = 660
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(40, y, "Item")
    pdf.drawString(300, y, "Qty")
    pdf.drawString(340, y, "Unit")
    pdf.drawString(420, y, "Total")
    y -= 15

    pdf.setFont("Helvetica", 10)
    for item in items:
        if y < 80:
            pdf.showPage()
            y = 740
        pdf.drawString(40, y, item["product_name"])
        pdf.drawRightString(320, y, str(item["quantity"]))
        pdf.drawRightString(390, y, item["unit_price"])
        pdf.drawRightString(470, y, item["line_total"])
        y -= 15

    y -= 10
    pdf.drawRightString(470, y, f"Subtotal: {order['subtotal']}")
    y -= 15
    pdf.drawRightString(470, y, f"Discount: {order['discount_amount']}")
    y -= 15
    pdf.drawRightString(470, y, f"Shipping: {order['shipping_amount']}")
    y -= 15
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawRightString(470, y, f"Total: {order['total_amount']}")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@router.post("")
def place_order(payload: OrderCreateRequest, user=Depends(get_current_user)):
    """Place an order from the user's cart."""
    settings = get_settings()

    with get_connection() as conn:
        with conn.transaction():
            cart_id, applied_coupon_id = _load_cart(conn, user["id"])
            items = _load_cart_items(conn, cart_id)
            if not items:
                raise HTTPException(status_code=400, detail="Cart is empty")

            branch_id = _ensure_single_branch(items)
            subtotal = sum(
                (item["line_total"] for item in items), Decimal("0")
            )
            shipping_amount = Decimal("0")

            coupon_row = None
            if applied_coupon_id:
                coupon_row = conn.execute(
                    """
                    SELECT id, code, type, value, min_order, max_uses, expires_at, is_active
                    FROM coupons
                    WHERE id = %s
                    """,
                    (applied_coupon_id,),
                ).fetchone()
                if coupon_row:
                    coupon_row = validate_coupon_for_cart(
                        conn, user["id"], coupon_row[1], subtotal
                    )

            discount_amount = calculate_discount(
                coupon_row, subtotal, shipping_amount
            )
            total_amount = max(
                subtotal - discount_amount + shipping_amount, Decimal("0")
            )

            address_snapshot = _load_address_snapshot(
                conn, payload.address_id, user["id"]
            )
            reference = _next_order_reference(conn)

            reservation_key = f"cart:{cart_id}"
            reservation_rows = conn.execute(
                """
                SELECT product_id, branch_id, SUM(quantity)
                FROM inventory_reservations
                WHERE reservation_key = %s
                GROUP BY product_id, branch_id
                """,
                (reservation_key,),
            ).fetchall()
            reserved_map = {
                (str(row[0]), str(row[1])): row[2] for row in reservation_rows
            }

            for item in items:
                reserved_qty = reserved_map.get(
                    (item["product_id"], item["branch_id"]), 0
                )
                updated = conn.execute(
                    """
                    UPDATE inventory
                    SET
                      quantity = quantity - %s,
                      reserved_qty = GREATEST(reserved_qty - %s, 0),
                      updated_at = NOW()
                    WHERE product_id = %s
                      AND branch_id = %s
                      AND quantity >= %s
                      AND (quantity - reserved_qty + %s) >= %s
                    RETURNING quantity
                    """,
                    (
                        item["quantity"],
                        reserved_qty,
                        item["product_id"],
                        item["branch_id"],
                        item["quantity"],
                        reserved_qty,
                        item["quantity"],
                    ),
                ).fetchone()
                if not updated:
                    raise HTTPException(status_code=409, detail="Out of stock")

            order_row = conn.execute(
                """
                INSERT INTO orders (
                  reference,
                  user_id,
                  branch_id,
                  status,
                  total_amount,
                  discount_amount,
                  shipping_amount,
                  payment_status,
                  payment_method,
                  payment_ref,
                  delivery_address,
                  notes,
                  coupon_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    reference,
                    user["id"],
                    branch_id,
                    "placed",
                    total_amount,
                    discount_amount,
                    shipping_amount,
                    "pending",
                    payload.payment_method,
                    payload.payment_ref,
                    json.dumps(address_snapshot),
                    payload.notes,
                    coupon_row[0] if coupon_row else None,
                ),
            ).fetchone()
            order_id = str(order_row[0])

            conn.executemany(
                """
                INSERT INTO order_items (
                  order_id,
                  product_id,
                  quantity,
                  unit_price,
                  total_price
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (
                        order_id,
                        item["product_id"],
                        item["quantity"],
                        item["unit_price"],
                        item["line_total"],
                    )
                    for item in items
                ],
            )

            conn.execute(
                """
                INSERT INTO order_status_history (
                  order_id,
                  status,
                  changed_by,
                  note
                )
                VALUES (%s, %s, %s, %s)
                """,
                (order_id, "placed", user["id"], "Order placed"),
            )

            if coupon_row:
                try:
                    conn.execute(
                        """
                        INSERT INTO coupon_redemptions (
                          coupon_id,
                          user_id,
                          order_id
                        )
                        VALUES (%s, %s, %s)
                        """,
                        (coupon_row[0], user["id"], order_id),
                    )
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=409, detail="Coupon already used"
                    ) from exc

            conn.execute(
                "DELETE FROM inventory_reservations WHERE reservation_key = %s",
                (reservation_key,),
            )
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = %s",
                (cart_id,),
            )
            conn.execute(
                """
                UPDATE carts
                SET applied_coupon_id = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (cart_id,),
            )

        for item in items:
            update_product_stock(item["product_id"])

    event_payload = {
        "order_id": order_id,
        "reference": reference,
        "user_id": user["id"],
        "total_amount": str(total_amount),
        "items": [
            {
                "product_id": item["product_id"],
                "quantity": item["quantity"],
                "unit_price": str(item["unit_price"]),
            }
            for item in items
        ],
    }
    publish_message(settings.order_placed_queue_name, json.dumps(event_payload))
    return {"order_id": order_id, "reference": reference}


@router.get("")
def list_orders(
    page: int = 1,
    per_page: int = 24,
    user=Depends(get_current_user),
):
    """List orders for the authenticated user."""
    offset = max(page - 1, 0) * per_page
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, reference, status, total_amount, payment_status, created_at
            FROM orders
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (user["id"], per_page, offset),
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "reference": row[1],
            "status": row[2],
            "total_amount": str(row[3]),
            "payment_status": row[4],
            "created_at": row[5].isoformat(),
        }
        for row in rows
    ]


@router.get("/{order_id}")
def get_order(order_id: str, user=Depends(get_current_user)):
    """Return order details with items and status history."""
    with get_connection() as conn:
        order = conn.execute(
            """
            SELECT
              id,
              reference,
              status,
              total_amount,
              discount_amount,
              shipping_amount,
              payment_status,
              payment_method,
              payment_ref,
              delivery_address,
              created_at
            FROM orders
            WHERE id = %s AND user_id = %s
            """,
            (order_id, user["id"]),
        ).fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        items = conn.execute(
            """
            SELECT oi.product_id, p.name, oi.quantity, oi.unit_price, oi.total_price
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = %s
            """,
            (order_id,),
        ).fetchall()

        history = conn.execute(
            """
            SELECT status, changed_by, changed_at, note
            FROM order_status_history
            WHERE order_id = %s
            ORDER BY changed_at ASC
            """,
            (order_id,),
        ).fetchall()

    return {
        "id": str(order[0]),
        "reference": order[1],
        "status": order[2],
        "total_amount": str(order[3]),
        "discount_amount": str(order[4]),
        "shipping_amount": str(order[5]),
        "payment_status": order[6],
        "payment_method": order[7],
        "payment_ref": order[8],
        "delivery_address": order[9],
        "created_at": order[10].isoformat(),
        "items": [
            {
                "product_id": str(row[0]),
                "product_name": row[1],
                "quantity": row[2],
                "unit_price": str(row[3]),
                "total_price": str(row[4]),
            }
            for row in items
        ],
        "status_history": [
            {
                "status": row[0],
                "changed_by": str(row[1]) if row[1] else None,
                "changed_at": row[2].isoformat(),
                "note": row[3],
            }
            for row in history
        ],
    }


@router.get("/{order_id}/invoice")
def get_invoice(order_id: str, user=Depends(get_current_user)):
    """Generate a PDF invoice for the order."""
    with get_connection() as conn:
        order = conn.execute(
            """
            SELECT
              o.id,
              o.reference,
              o.total_amount,
              o.discount_amount,
              o.shipping_amount,
              o.created_at,
              u.name
            FROM orders o
            JOIN users u ON u.id = o.user_id
            WHERE o.id = %s AND o.user_id = %s
            """,
            (order_id, user["id"]),
        ).fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        items = conn.execute(
            """
            SELECT p.name, oi.quantity, oi.unit_price, oi.total_price
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = %s
            """,
            (order_id,),
        ).fetchall()

    pdf_bytes = _build_invoice_pdf(
        {
            "reference": order[1],
            "total_amount": str(order[2]),
            "discount_amount": str(order[3]),
            "shipping_amount": str(order[4]),
            "subtotal": str(order[2] + order[3] - order[4]),
            "created_at": order[5].strftime("%Y-%m-%d"),
            "customer_name": order[6],
        },
        [
            {
                "product_name": row[0],
                "quantity": row[1],
                "unit_price": str(row[2]),
                "line_total": str(row[3]),
            }
            for row in items
        ],
    )
    headers = {
        "Content-Disposition": f"attachment; filename=invoice-{order[1]}.pdf"
    }
    return Response(
        pdf_bytes, media_type="application/pdf", headers=headers
    )


@router.post("/{order_id}/cancel")
def cancel_order(order_id: str, user=Depends(get_current_user)):
    """Cancel a placed order and release inventory."""
    settings = get_settings()
    with get_connection() as conn:
        with conn.transaction():
            order = conn.execute(
                """
                SELECT id, status, payment_status, branch_id
                FROM orders
                WHERE id = %s AND user_id = %s
                """,
                (order_id, user["id"]),
            ).fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")
            if order[1] != "placed":
                raise HTTPException(
                    status_code=422, detail="Order cannot be cancelled"
                )

            items = conn.execute(
                """
                SELECT product_id, quantity
                FROM order_items
                WHERE order_id = %s
                """,
                (order_id,),
            ).fetchall()

            for item in items:
                conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = quantity + %s, updated_at = NOW()
                    WHERE product_id = %s AND branch_id = %s
                    """,
                    (item[1], item[0], order[3]),
                )

            conn.execute(
                """
                UPDATE orders
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                """,
                ("cancelled", order_id),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (
                  order_id,
                  status,
                  changed_by,
                  note
                )
                VALUES (%s, %s, %s, %s)
                """,
                (order_id, "cancelled", user["id"], "Customer cancelled"),
            )

        for item in items:
            update_product_stock(str(item[0]))

    if order[2] == "paid":
        publish_message(
            settings.refund_queue_name,
            json.dumps(
                {
                    "order_id": order_id,
                    "reason": "customer_cancelled",
                }
            ),
        )
    return {"status": "cancelled"}


@admin_router.get("")
def admin_list_orders(
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    per_page: int = 24,
    user=Depends(require_admin),
):
    """List orders for admin with filtering options."""
    offset = max(page - 1, 0) * per_page
    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if branch_id:
        conditions.append("branch_id = %s")
        params.append(branch_id)
    if search:
        conditions.append("reference ILIKE %s")
        params.append(f"%{search}%")
    if start_date:
        conditions.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("created_at <= %s")
        params.append(end_date)

    where_clause = " AND ".join(conditions) or "TRUE"
    params.extend([per_page, offset])

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, reference, status, total_amount, payment_status, created_at
            FROM orders
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "reference": row[1],
            "status": row[2],
            "total_amount": str(row[3]),
            "payment_status": row[4],
            "created_at": row[5].isoformat(),
        }
        for row in rows
    ]


@admin_router.get("/export")
def export_orders(
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user=Depends(require_admin),
):
    """Export filtered orders as CSV for admin reporting."""
    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if branch_id:
        conditions.append("branch_id = %s")
        params.append(branch_id)
    if search:
        conditions.append("reference ILIKE %s")
        params.append(f"%{search}%")
    if start_date:
        conditions.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("created_at <= %s")
        params.append(end_date)

    where_clause = " AND ".join(conditions) or "TRUE"
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT reference, status, total_amount, payment_status, created_at
            FROM orders
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT 5000
            """,
            params,
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["reference", "status", "total_amount", "payment_status", "created_at"]
    )
    for row in rows:
        writer.writerow(
            [row[0], row[1], str(row[2]), row[3], row[4].isoformat()]
        )

    headers = {
        "Content-Disposition": "attachment; filename=orders_export.csv"
    }
    return PlainTextResponse(output.getvalue(), headers=headers)


@admin_router.patch("/{order_id}/status")
def update_order_status(
    order_id: str,
    payload: OrderStatusUpdateRequest,
    user=Depends(require_admin),
):
    """Update an order status following the allowed transitions."""
    new_status = payload.status
    with get_connection() as conn:
        with conn.transaction():
            order = conn.execute(
                """
                SELECT status
                FROM orders
                WHERE id = %s
                """,
                (order_id,),
            ).fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            current_status = order[0]
            allowed = ALLOWED_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise HTTPException(
                    status_code=422, detail="Invalid status transition"
                )

            conn.execute(
                """
                UPDATE orders
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (new_status, order_id),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (
                  order_id,
                  status,
                  changed_by,
                  note
                )
                VALUES (%s, %s, %s, %s)
                """,
                (order_id, new_status, user["id"], payload.note),
            )

    return {"status": new_status}
