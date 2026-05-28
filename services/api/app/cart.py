import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from .coupons import calculate_discount, serialize_coupon, validate_coupon_for_cart
from .db import get_connection
from .deps import get_current_user, get_optional_user
from .redis_client import get_redis
from .search_index import update_product_stock

router = APIRouter(prefix="/cart", tags=["cart"])

CART_TTL_SECONDS = 7 * 24 * 60 * 60
RESERVATION_TTL_MINUTES = 15


class CartItemCreateRequest(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)
    branch_id: Optional[str] = None


class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(..., gt=0)
    branch_id: Optional[str] = None


class CartCouponRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)


def _cart_cache_key(session_id: str) -> str:
    """Build the Redis key for a guest cart."""
    return f"cart:{session_id}"


def _user_cart_cache_key(user_id: str) -> str:
    """Build the Redis key for a persisted user cart."""
    return f"cart:user:{user_id}"


def _cart_reservation_key(cart_id: str) -> str:
    """Namespace inventory reservations for a cart."""
    return f"cart:{cart_id}"


def _guest_reservation_key(session_id: str) -> str:
    """Namespace inventory reservations for a guest cart."""
    return f"guest:{session_id}"


def _load_cart_from_cache(cache_key: str) -> dict:
    """Load a cart payload from Redis or return an empty structure."""
    redis_client = get_redis()
    cached = redis_client.get(cache_key)
    if not cached:
        return {"items": []}
    return json.loads(cached)


def _save_cart_to_cache(cache_key: str, cart: dict) -> None:
    """Persist a cart payload to Redis with the configured TTL."""
    redis_client = get_redis()
    redis_client.setex(cache_key, CART_TTL_SECONDS, json.dumps(cart))


def _resolve_branch_id(conn, branch_id: Optional[str]) -> str:
    """Select a valid branch id, defaulting to the first active branch."""
    if branch_id:
        row = conn.execute(
            "SELECT id FROM branches WHERE id = %s",
            (branch_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Branch not found")
        return str(row[0])

    row = conn.execute(
        "SELECT id FROM branches WHERE is_active = TRUE ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="No active branch available")
    return str(row[0])


def _ensure_product(conn, product_id: str, require_active: bool = True) -> tuple:
    """Fetch product details or raise when unavailable."""
    row = conn.execute(
        """
        SELECT id, name, slug, price, original_price, status
        FROM products
        WHERE id = %s
        """,
        (product_id,),
    ).fetchone()
    if not row or (require_active and row[5] != "active"):
        raise HTTPException(status_code=404, detail="Product not found")
    return row


def _get_or_create_cart(conn, user_id: str) -> tuple[str, Optional[str]]:
    """Load the user's cart or create one if missing."""
    row = conn.execute(
        "SELECT id, applied_coupon_id FROM carts WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    if row:
        return str(row[0]), str(row[1]) if row[1] else None

    created = conn.execute(
        """
        INSERT INTO carts (user_id)
        VALUES (%s)
        RETURNING id
        """,
        (user_id,),
    ).fetchone()
    return str(created[0]), None


def _list_cart_items(conn, cart_id: str) -> list[dict]:
    """Return enriched cart item data for responses."""
    rows = conn.execute(
        """
        SELECT
          ci.id,
          ci.product_id,
          ci.branch_id,
          ci.quantity,
          ci.reservation_id,
          p.name,
          p.slug,
          p.price,
          p.original_price
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        WHERE ci.cart_id = %s
        ORDER BY ci.created_at ASC
        """,
        (cart_id,),
    ).fetchall()

    items: list[dict] = []
    for row in rows:
        unit_price = row[7]
        line_total = unit_price * row[3]
        items.append(
            {
                "id": str(row[0]),
                "product_id": str(row[1]),
                "branch_id": str(row[2]) if row[2] else None,
                "quantity": row[3],
                "reservation_id": str(row[4]) if row[4] else None,
                "product_name": row[5],
                "product_slug": row[6],
                "unit_price": str(unit_price),
                "original_price": str(row[8]) if row[8] else None,
                "line_total": str(line_total),
            }
        )
    return items


def _calculate_totals(
    items: list[dict], coupon_row: Optional[tuple]
) -> dict:
    """Compute cart subtotal, discount, and grand total."""
    subtotal = sum(
        (Decimal(item["line_total"]) for item in items), Decimal("0")
    )
    shipping_amount = Decimal("0")
    discount = calculate_discount(coupon_row, subtotal, shipping_amount)
    total = max(subtotal - discount + shipping_amount, Decimal("0"))
    return {
        "subtotal": str(subtotal),
        "discount": str(discount),
        "shipping_amount": str(shipping_amount),
        "total": str(total),
    }


def _create_reservation(
    conn,
    product_id: str,
    branch_id: str,
    quantity: int,
    reservation_key: str,
) -> str:
    """Reserve inventory stock and return the reservation id."""
    reservation_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=RESERVATION_TTL_MINUTES
    )

    updated = conn.execute(
        """
        UPDATE inventory
        SET reserved_qty = reserved_qty + %s, updated_at = NOW()
        WHERE product_id = %s
          AND branch_id = %s
          AND (quantity - reserved_qty) >= %s
        RETURNING quantity, reserved_qty
        """,
        (quantity, product_id, branch_id, quantity),
    ).fetchone()
    if not updated:
        raise HTTPException(status_code=409, detail="Out of stock")

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
        (reservation_id, product_id, branch_id, quantity, reservation_key, expires_at),
    )
    return reservation_id


def _update_reservation(
    conn, reservation_id: str, new_quantity: int
) -> None:
    """Update an existing reservation quantity."""
    row = conn.execute(
        """
        SELECT product_id, branch_id, quantity
        FROM inventory_reservations
        WHERE id = %s
        """,
        (reservation_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")

    product_id, branch_id, current_qty = row
    delta = new_quantity - current_qty
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=RESERVATION_TTL_MINUTES
    )

    if delta > 0:
        updated = conn.execute(
            """
            UPDATE inventory
            SET reserved_qty = reserved_qty + %s, updated_at = NOW()
            WHERE product_id = %s
              AND branch_id = %s
              AND (quantity - reserved_qty) >= %s
            RETURNING quantity
            """,
            (delta, product_id, branch_id, delta),
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Out of stock")
    elif delta < 0:
        conn.execute(
            """
            UPDATE inventory
            SET reserved_qty = GREATEST(reserved_qty + %s, 0),
                updated_at = NOW()
            WHERE product_id = %s AND branch_id = %s
            """,
            (delta, product_id, branch_id),
        )

    conn.execute(
        """
        UPDATE inventory_reservations
        SET quantity = %s, expires_at = %s
        WHERE id = %s
        """,
        (new_quantity, expires_at, reservation_id),
    )


def _release_reservation(conn, reservation_id: str) -> None:
    """Release a single inventory reservation."""
    row = conn.execute(
        """
        SELECT product_id, branch_id, quantity
        FROM inventory_reservations
        WHERE id = %s
        """,
        (reservation_id,),
    ).fetchone()
    if not row:
        return

    product_id, branch_id, quantity = row
    conn.execute(
        """
        UPDATE inventory
        SET reserved_qty = GREATEST(reserved_qty - %s, 0),
            updated_at = NOW()
        WHERE product_id = %s AND branch_id = %s
        """,
        (quantity, product_id, branch_id),
    )
    conn.execute(
        "DELETE FROM inventory_reservations WHERE id = %s",
        (reservation_id,),
    )


def _release_reservations_by_key(conn, reservation_key: str) -> list[str]:
    """Release all reservations tied to a reservation key."""
    rows = conn.execute(
        """
        SELECT id, product_id, branch_id, quantity
        FROM inventory_reservations
        WHERE reservation_key = %s
        """,
        (reservation_key,),
    ).fetchall()
    if not rows:
        return []

    grouped: dict[tuple[str, str], int] = {}
    reservation_ids = []
    for row in rows:
        reservation_ids.append(row[0])
        key = (str(row[1]), str(row[2]))
        grouped[key] = grouped.get(key, 0) + row[3]

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

    conn.execute(
        "DELETE FROM inventory_reservations WHERE id = ANY(%s)",
        (reservation_ids,),
    )
    return [pid for pid, _bid in grouped.keys()]


def _sync_user_cart_cache(user_id: str, items: list[dict]) -> None:
    """Push the latest cart snapshot to Redis for logged-in users."""
    cache_key = _user_cart_cache_key(user_id)
    _save_cart_to_cache(cache_key, {"items": items})


def _build_cart_response(
    cart_id: Optional[str], items: list[dict], coupon_row: Optional[tuple]
) -> dict:
    """Build the response payload for cart endpoints."""
    totals = _calculate_totals(items, coupon_row)
    return {
        "cart_id": cart_id,
        "items": items,
        "coupon": serialize_coupon(coupon_row) if coupon_row else None,
        **totals,
    }


def _fetch_coupon_row(conn, coupon_id: Optional[str]):
    """Load a coupon row by id if available."""
    if not coupon_id:
        return None
    return conn.execute(
        """
        SELECT id, code, type, value, min_order, max_uses, expires_at, is_active
        FROM coupons
        WHERE id = %s
        """,
        (coupon_id,),
    ).fetchone()


def _ensure_coupon_valid(
    conn,
    cart_id: str,
    user_id: str,
    coupon_row: Optional[tuple],
    subtotal: Decimal,
):
    """Revalidate an applied coupon against the latest cart subtotal."""
    if not coupon_row:
        return None
    try:
        return validate_coupon_for_cart(conn, user_id, coupon_row[1], subtotal)
    except HTTPException:
        conn.execute(
            "UPDATE carts SET applied_coupon_id = NULL WHERE id = %s",
            (cart_id,),
        )
        return None


def _merge_guest_cart(
    conn, session_id: str, cart_id: str
) -> tuple[list[dict], list[str]]:
    """Merge guest cart items into a user cart."""
    guest_cart = _load_cart_from_cache(_cart_cache_key(session_id))
    guest_items = guest_cart.get("items", [])
    if not guest_items:
        return [], []

    skipped: list[dict] = []
    touched_products: set[str] = set()
    reservation_key = _cart_reservation_key(cart_id)
    released = _release_reservations_by_key(
        conn, _guest_reservation_key(session_id)
    )
    touched_products.update(released)

    for item in guest_items:
        try:
            _upsert_cart_item(
                conn,
                cart_id,
                item["product_id"],
                item["branch_id"],
                item["quantity"],
                reservation_key,
            )
            touched_products.add(item["product_id"])
        except HTTPException as exc:
            if exc.status_code == 409:
                skipped.append(item)
                continue
            raise

    return skipped, list(touched_products)


def _upsert_cart_item(
    conn,
    cart_id: str,
    product_id: str,
    branch_id: str,
    quantity: int,
    reservation_key: str,
) -> None:
    """Insert or update a cart item while maintaining reservations."""
    _ensure_product(conn, product_id)
    existing = conn.execute(
        """
        SELECT id, quantity, reservation_id
        FROM cart_items
        WHERE cart_id = %s AND product_id = %s AND branch_id = %s
        """,
        (cart_id, product_id, branch_id),
    ).fetchone()

    if existing:
        new_quantity = existing[1] + quantity
        _update_reservation(conn, str(existing[2]), new_quantity)
        conn.execute(
            """
            UPDATE cart_items
            SET quantity = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (new_quantity, existing[0]),
        )
    else:
        reservation_id = _create_reservation(
            conn, product_id, branch_id, quantity, reservation_key
        )
        conn.execute(
            """
            INSERT INTO cart_items (
              cart_id,
              product_id,
              branch_id,
              quantity,
              reservation_id
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (cart_id, product_id, branch_id, quantity, reservation_id),
        )


@router.get("")
def get_cart(
    session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    user=Depends(get_optional_user),
):
    """Fetch the current cart for a guest session or logged-in user."""
    if not user and not session_id:
        raise HTTPException(
            status_code=400, detail="Session id is required for guests"
        )

    with get_connection() as conn:
        if user:
            cart_id, applied_coupon_id = _get_or_create_cart(conn, user["id"])
            skipped_items: list[dict] = []
            touched_products: list[str] = []
            if session_id:
                skipped_items, touched_products = _merge_guest_cart(
                    conn, session_id, cart_id
                )

            items = _list_cart_items(conn, cart_id)
            subtotal = sum(
                (Decimal(item["line_total"]) for item in items), Decimal("0")
            )
            coupon_row = _fetch_coupon_row(conn, applied_coupon_id)
            coupon_row = _ensure_coupon_valid(
                conn, cart_id, user["id"], coupon_row, subtotal
            )

            response = _build_cart_response(cart_id, items, coupon_row)
            if skipped_items:
                response["warnings"] = {
                    "skipped_items": skipped_items,
                    "message": "Some guest items were out of stock",
                }

            _sync_user_cart_cache(user["id"], items)
            if session_id:
                cache_key = _cart_cache_key(session_id)
                get_redis().delete(cache_key)
            for product_id in touched_products:
                update_product_stock(product_id)
            return response

        guest_cart = _load_cart_from_cache(_cart_cache_key(session_id))
        guest_items = guest_cart.get("items", [])
        items: list[dict] = []
        for item in guest_items:
            product = _ensure_product(conn, item["product_id"], require_active=False)
            unit_price = product[3]
            line_total = unit_price * item["quantity"]
            items.append(
                {
                    "product_id": str(product[0]),
                    "branch_id": item["branch_id"],
                    "quantity": item["quantity"],
                    "reservation_id": item.get("reservation_id"),
                    "product_name": product[1],
                    "product_slug": product[2],
                    "unit_price": str(unit_price),
                    "original_price": str(product[4]) if product[4] else None,
                    "line_total": str(line_total),
                }
            )

        response = _build_cart_response(None, items, None)
        return response


@router.post("/items")
def add_cart_item(
    payload: CartItemCreateRequest,
    session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    user=Depends(get_optional_user),
):
    """Add an item to the cart with a stock reservation."""
    if not user and not session_id:
        raise HTTPException(
            status_code=400, detail="Session id is required for guests"
        )

    with get_connection() as conn:
        branch_id = _resolve_branch_id(conn, payload.branch_id)
        if user:
            with conn.transaction():
                cart_id, applied_coupon_id = _get_or_create_cart(
                    conn, user["id"]
                )
                _upsert_cart_item(
                    conn,
                    cart_id,
                    payload.product_id,
                    branch_id,
                    payload.quantity,
                    _cart_reservation_key(cart_id),
                )

            items = _list_cart_items(conn, cart_id)
            subtotal = sum(
                (Decimal(item["line_total"]) for item in items),
                Decimal("0"),
            )
            coupon_row = _fetch_coupon_row(conn, applied_coupon_id)
            coupon_row = _ensure_coupon_valid(
                conn, cart_id, user["id"], coupon_row, subtotal
            )
            _sync_user_cart_cache(user["id"], items)
            update_product_stock(payload.product_id)
            return _build_cart_response(cart_id, items, coupon_row)

        cart_key = _cart_cache_key(session_id)
        cart = _load_cart_from_cache(cart_key)
        items = cart.get("items", [])
        existing = next(
            (
                item
                for item in items
                if item["product_id"] == payload.product_id
                and item["branch_id"] == branch_id
            ),
            None,
        )
        with conn.transaction():
            if existing:
                new_quantity = existing["quantity"] + payload.quantity
                _update_reservation(conn, existing["reservation_id"], new_quantity)
                existing["quantity"] = new_quantity
            else:
                reservation_id = _create_reservation(
                    conn,
                    payload.product_id,
                    branch_id,
                    payload.quantity,
                    _guest_reservation_key(session_id),
                )
                items.append(
                    {
                        "product_id": payload.product_id,
                        "branch_id": branch_id,
                        "quantity": payload.quantity,
                        "reservation_id": reservation_id,
                    }
                )

        cart["items"] = items
        _save_cart_to_cache(cart_key, cart)
        update_product_stock(payload.product_id)
        response = _build_cart_response(None, _hydrate_guest_items(conn, items), None)
        return response


def _hydrate_guest_items(conn, items: list[dict]) -> list[dict]:
    """Attach product data to guest cart items."""
    enriched: list[dict] = []
    for item in items:
        product = _ensure_product(conn, item["product_id"], require_active=False)
        unit_price = product[3]
        line_total = unit_price * item["quantity"]
        enriched.append(
            {
                "product_id": str(product[0]),
                "branch_id": item["branch_id"],
                "quantity": item["quantity"],
                "reservation_id": item.get("reservation_id"),
                "product_name": product[1],
                "product_slug": product[2],
                "unit_price": str(unit_price),
                "original_price": str(product[4]) if product[4] else None,
                "line_total": str(line_total),
            }
        )
    return enriched


@router.patch("/items/{product_id}")
def update_cart_item(
    product_id: str,
    payload: CartItemUpdateRequest,
    session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    user=Depends(get_optional_user),
):
    """Update the quantity of a cart item."""
    if not user and not session_id:
        raise HTTPException(
            status_code=400, detail="Session id is required for guests"
        )

    with get_connection() as conn:
        if user:
            cart_id, applied_coupon_id = _get_or_create_cart(conn, user["id"])
            if payload.branch_id:
                branch_id = _resolve_branch_id(conn, payload.branch_id)
                row = conn.execute(
                    """
                    SELECT id, reservation_id, branch_id
                    FROM cart_items
                    WHERE cart_id = %s AND product_id = %s AND branch_id = %s
                    """,
                    (cart_id, product_id, branch_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, reservation_id, branch_id
                    FROM cart_items
                    WHERE cart_id = %s AND product_id = %s
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (cart_id, product_id),
                ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Cart item not found")

            with conn.transaction():
                _update_reservation(conn, str(row[1]), payload.quantity)
                conn.execute(
                    """
                    UPDATE cart_items
                    SET quantity = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (payload.quantity, row[0]),
                )

            items = _list_cart_items(conn, cart_id)
            subtotal = sum(
                (Decimal(item["line_total"]) for item in items),
                Decimal("0"),
            )
            coupon_row = _fetch_coupon_row(conn, applied_coupon_id)
            coupon_row = _ensure_coupon_valid(
                conn, cart_id, user["id"], coupon_row, subtotal
            )
            _sync_user_cart_cache(user["id"], items)
            update_product_stock(product_id)
            return _build_cart_response(cart_id, items, coupon_row)

        cart_key = _cart_cache_key(session_id)
        cart = _load_cart_from_cache(cart_key)
        items = cart.get("items", [])
        if payload.branch_id:
            branch_id = _resolve_branch_id(conn, payload.branch_id)
            existing = next(
                (
                    item
                    for item in items
                    if item["product_id"] == product_id
                    and item["branch_id"] == branch_id
                ),
                None,
            )
        else:
            existing = next(
                (item for item in items if item["product_id"] == product_id),
                None,
            )
        if not existing:
            raise HTTPException(status_code=404, detail="Cart item not found")

        with conn.transaction():
            _update_reservation(conn, existing["reservation_id"], payload.quantity)
            existing["quantity"] = payload.quantity

        cart["items"] = items
        _save_cart_to_cache(cart_key, cart)
        update_product_stock(product_id)
        response = _build_cart_response(None, _hydrate_guest_items(conn, items), None)
        return response


@router.delete("/items/{product_id}")
def remove_cart_item(
    product_id: str,
    branch_id: Optional[str] = None,
    session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    user=Depends(get_optional_user),
):
    """Remove an item from the cart and release its reservation."""
    if not user and not session_id:
        raise HTTPException(
            status_code=400, detail="Session id is required for guests"
        )

    with get_connection() as conn:
        if user:
            cart_id, applied_coupon_id = _get_or_create_cart(conn, user["id"])
            if branch_id:
                resolved_branch_id = _resolve_branch_id(conn, branch_id)
                row = conn.execute(
                    """
                    SELECT id, reservation_id
                    FROM cart_items
                    WHERE cart_id = %s AND product_id = %s AND branch_id = %s
                    """,
                    (cart_id, product_id, resolved_branch_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, reservation_id, branch_id
                    FROM cart_items
                    WHERE cart_id = %s AND product_id = %s
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (cart_id, product_id),
                ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Cart item not found")

            with conn.transaction():
                _release_reservation(conn, str(row[1]))
                conn.execute(
                    "DELETE FROM cart_items WHERE id = %s",
                    (row[0],),
                )

            items = _list_cart_items(conn, cart_id)
            subtotal = sum(
                (Decimal(item["line_total"]) for item in items),
                Decimal("0"),
            )
            coupon_row = _fetch_coupon_row(conn, applied_coupon_id)
            coupon_row = _ensure_coupon_valid(
                conn, cart_id, user["id"], coupon_row, subtotal
            )
            _sync_user_cart_cache(user["id"], items)
            update_product_stock(product_id)
            return _build_cart_response(cart_id, items, coupon_row)

        cart_key = _cart_cache_key(session_id)
        cart = _load_cart_from_cache(cart_key)
        items = cart.get("items", [])
        if branch_id:
            resolved_branch_id = _resolve_branch_id(conn, branch_id)
            existing = next(
                (
                    item
                    for item in items
                    if item["product_id"] == product_id
                    and item["branch_id"] == resolved_branch_id
                ),
                None,
            )
        else:
            existing = next(
                (item for item in items if item["product_id"] == product_id),
                None,
            )
        if not existing:
            raise HTTPException(status_code=404, detail="Cart item not found")

        with conn.transaction():
            _release_reservation(conn, existing["reservation_id"])
            items.remove(existing)

        cart["items"] = items
        _save_cart_to_cache(cart_key, cart)
        update_product_stock(product_id)
        response = _build_cart_response(None, _hydrate_guest_items(conn, items), None)
        return response


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_cart(
    session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    user=Depends(get_optional_user),
):
    """Clear all cart items and reservations."""
    if not user and not session_id:
        raise HTTPException(
            status_code=400, detail="Session id is required for guests"
        )

    with get_connection() as conn:
        if user:
            cart_id, _coupon_id = _get_or_create_cart(conn, user["id"])
            with conn.transaction():
                product_ids = _release_reservations_by_key(
                    conn, _cart_reservation_key(cart_id)
                )
                conn.execute(
                    "DELETE FROM cart_items WHERE cart_id = %s",
                    (cart_id,),
                )
                conn.execute(
                    "UPDATE carts SET applied_coupon_id = NULL WHERE id = %s",
                    (cart_id,),
                )
            for product_id in product_ids:
                update_product_stock(product_id)
            _sync_user_cart_cache(user["id"], [])
            return None

        cart_key = _cart_cache_key(session_id)
        cart = _load_cart_from_cache(cart_key)
        items = cart.get("items", [])
        with conn.transaction():
            _release_reservations_by_key(conn, _guest_reservation_key(session_id))
        _save_cart_to_cache(cart_key, {"items": []})
        for item in items:
            update_product_stock(item["product_id"])
        return None


@router.post("/coupon")
def apply_coupon(
    payload: CartCouponRequest, user=Depends(get_current_user)
):
    """Apply a coupon to the authenticated user's cart."""
    with get_connection() as conn:
        cart_id, _coupon_id = _get_or_create_cart(conn, user["id"])
        items = _list_cart_items(conn, cart_id)
        if not items:
            raise HTTPException(status_code=400, detail="Cart is empty")

        subtotal = sum(
            (Decimal(item["line_total"]) for item in items), Decimal("0")
        )
        coupon_row = validate_coupon_for_cart(
            conn, user["id"], payload.code, subtotal
        )
        conn.execute(
            """
            UPDATE carts
            SET applied_coupon_id = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (coupon_row[0], cart_id),
        )

        _sync_user_cart_cache(user["id"], items)
        response = _build_cart_response(cart_id, items, coupon_row)
        return response


@router.delete("/coupon")
def remove_coupon(user=Depends(get_current_user)):
    """Remove the coupon from the authenticated user's cart."""
    with get_connection() as conn:
        cart_id, _coupon_id = _get_or_create_cart(conn, user["id"])
        conn.execute(
            """
            UPDATE carts
            SET applied_coupon_id = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (cart_id,),
        )
        items = _list_cart_items(conn, cart_id)
        _sync_user_cart_cache(user["id"], items)
        response = _build_cart_response(cart_id, items, None)
        return response
