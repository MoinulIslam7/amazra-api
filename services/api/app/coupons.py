from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .db import get_connection
from .deps import require_admin

router = APIRouter(prefix="/coupons", tags=["coupons"])

ALLOWED_COUPON_TYPES = {"percentage", "fixed", "free_shipping"}


class CouponCreateRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    type: str = Field(..., min_length=3, max_length=20)
    value: Decimal = Field(Decimal("0"), ge=0)
    min_order: Optional[Decimal] = Field(None, ge=0)
    max_uses: Optional[int] = Field(None, ge=1)
    expires_at: Optional[datetime] = None
    is_active: bool = True


def normalize_coupon_code(code: str) -> str:
    """Normalize coupon codes for consistent storage and lookups."""
    return code.strip().upper()


def _validate_coupon_type(coupon_type: str) -> None:
    """Ensure coupon type matches the supported set."""
    if coupon_type not in ALLOWED_COUPON_TYPES:
        raise HTTPException(status_code=400, detail="Invalid coupon type")


def fetch_coupon_by_code(conn, code: str):
    """Fetch a coupon row by code."""
    return conn.execute(
        """
        SELECT id, code, type, value, min_order, max_uses, expires_at, is_active
        FROM coupons
        WHERE code = %s
        """,
        (normalize_coupon_code(code),),
    ).fetchone()


def validate_coupon_for_cart(
    conn, user_id: str, code: str, subtotal: Decimal
):
    """Validate coupon rules against the current cart subtotal."""
    coupon = fetch_coupon_by_code(conn, code)
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")

    coupon_id, _code, coupon_type, _value, min_order, max_uses, expires_at, is_active = (
        coupon
    )
    if not is_active:
        raise HTTPException(status_code=404, detail="Coupon not found")

    now = datetime.now(timezone.utc)
    if expires_at and expires_at < now:
        raise HTTPException(status_code=410, detail="Coupon expired")

    if max_uses is not None:
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id = %s",
            (coupon_id,),
        ).fetchone()[0]
        if usage_count >= max_uses:
            raise HTTPException(status_code=409, detail="Coupon exhausted")

    if min_order and subtotal < min_order:
        raise HTTPException(status_code=422, detail="Minimum order not met")

    used_by_user = conn.execute(
        """
        SELECT 1 FROM coupon_redemptions
        WHERE coupon_id = %s AND user_id = %s
        """,
        (coupon_id, user_id),
    ).fetchone()
    if used_by_user:
        raise HTTPException(status_code=409, detail="Coupon already used")

    _validate_coupon_type(coupon_type)
    return coupon


def calculate_discount(
    coupon_row, subtotal: Decimal, shipping_amount: Decimal
) -> Decimal:
    """Calculate discount amount for the given coupon."""
    if not coupon_row:
        return Decimal("0")

    _coupon_id, _code, coupon_type, value, _min_order, _max_uses, _expires_at, _is_active = (
        coupon_row
    )
    if coupon_type == "percentage":
        return (subtotal * value) / Decimal("100")
    if coupon_type == "fixed":
        return min(value, subtotal)
    if coupon_type == "free_shipping":
        return shipping_amount
    return Decimal("0")


def serialize_coupon(coupon_row) -> dict:
    """Serialize coupon metadata for API responses."""
    (
        coupon_id,
        code,
        coupon_type,
        value,
        min_order,
        max_uses,
        expires_at,
        is_active,
    ) = coupon_row
    return {
        "id": str(coupon_id),
        "code": code,
        "type": coupon_type,
        "value": str(value),
        "min_order": str(min_order) if min_order is not None else None,
        "max_uses": max_uses,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "is_active": is_active,
    }


@router.post("")
def create_coupon(payload: CouponCreateRequest, user=Depends(require_admin)):
    """Create a new coupon for checkout discounts."""
    coupon_code = normalize_coupon_code(payload.code)
    _validate_coupon_type(payload.type)

    with get_connection() as conn:
        try:
            row = conn.execute(
                """
                INSERT INTO coupons (
                  code,
                  type,
                  value,
                  min_order,
                  max_uses,
                  expires_at,
                  is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    coupon_code,
                    payload.type,
                    payload.value,
                    payload.min_order,
                    payload.max_uses,
                    payload.expires_at,
                    payload.is_active,
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409, detail="Coupon code already exists"
            ) from exc

    return {"id": str(row[0])}


@router.get("")
def list_coupons(user=Depends(require_admin)):
    """List all coupons with aggregate usage counts."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
              c.id,
              c.code,
              c.type,
              c.value,
              c.min_order,
              c.max_uses,
              c.expires_at,
              c.is_active,
              COALESCE(COUNT(r.id), 0) AS usage_count
            FROM coupons c
            LEFT JOIN coupon_redemptions r ON r.coupon_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """
        ).fetchall()

    return [
        {
            **serialize_coupon(row[:8]),
            "usage_count": row[8],
        }
        for row in rows
    ]
