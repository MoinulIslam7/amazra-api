"""Phase 5.1 + 5.2 — Delivery & Click-and-Collect.

Covers:
  - Pathao courier integration (create parcel, track)
  - Steadfast courier integration (create parcel, track)
  - Delivery webhooks (Pathao, Steadfast)
  - Shipping zones CRUD
  - Branch listing (public)
  - Click & Collect: ready-for-pickup + customer pickup confirmation
"""

import json
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .config import get_settings
from .db import get_connection
from .deps import get_current_user, require_admin
from .queue import publish_message
from .redis_client import get_redis

router = APIRouter(prefix="/delivery", tags=["delivery"])
admin_router = APIRouter(prefix="/admin/delivery", tags=["delivery"])
branches_router = APIRouter(prefix="/branches", tags=["branches"])

_PATHAO_TOKEN_KEY = "pathao:access_token"
_COURIER_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class DispatchRequest(BaseModel):
    courier: str = Field(..., pattern=r"^(pathao|steadfast)$")
    item_weight_kg: float = Field(0.5, gt=0, le=50)
    note: Optional[str] = Field(None, max_length=200)


class ZoneCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    districts: list[str] = Field(..., min_length=1)
    base_rate: Decimal = Field(..., ge=0)
    rate_per_kg: Decimal = Field(..., ge=0)


class ZoneUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    districts: Optional[list[str]] = None
    base_rate: Optional[Decimal] = Field(None, ge=0)
    rate_per_kg: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = None


# ---------------------------------------------------------------------------
# Pathao helpers
# ---------------------------------------------------------------------------


def _get_pathao_token(settings) -> str:
    """Return a valid Pathao OAuth access token, refreshing from Redis if expired."""
    redis = get_redis()
    cached = redis.get(_PATHAO_TOKEN_KEY)
    if cached:
        return cached.decode()

    if not all(
        [settings.pathao_client_id, settings.pathao_client_secret,
         settings.pathao_username, settings.pathao_password]
    ):
        raise HTTPException(status_code=503, detail="Pathao not configured")

    try:
        resp = httpx.post(
            f"{settings.pathao_base_url}/aladdin/api/v1/issue-token",
            json={
                "client_id": settings.pathao_client_id,
                "client_secret": settings.pathao_client_secret,
                "grant_type": "password",
                "username": settings.pathao_username,
                "password": settings.pathao_password,
            },
            timeout=_COURIER_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Pathao auth failed") from exc

    token = body.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="Pathao returned no access token")

    ttl = max(int(body.get("expires_in", 3600)) - 60, 60)
    redis.setex(_PATHAO_TOKEN_KEY, ttl, token)
    return token


def _create_pathao_parcel(order: dict, dispatch: DispatchRequest, settings) -> dict:
    token = _get_pathao_token(settings)
    addr = order["delivery_address"]
    try:
        resp = httpx.post(
            f"{settings.pathao_base_url}/aladdin/api/v1/orders/bulk",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "store_id": settings.pathao_store_id,
                "merchant_order_id": order["reference"],
                "recipient_name": addr.get("name", "Customer"),
                "recipient_phone": addr.get("phone", "01700000000"),
                "recipient_address": (
                    f"{addr.get('line1', '')} {addr.get('district', '')}".strip()
                ),
                "recipient_city": settings.pathao_default_city_id or 1,
                "recipient_zone": settings.pathao_default_zone_id or 1,
                "delivery_type": 48,
                "item_type": 2,
                "item_quantity": 1,
                "item_weight": dispatch.item_weight_kg,
                "amount_to_collect": (
                    float(order["total_amount"])
                    if order["payment_method"] == "cod"
                    else 0
                ),
                "item_description": f"Order {order['reference']}",
                "special_instruction": dispatch.note or "",
            },
            timeout=_COURIER_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Pathao unavailable") from exc


def _track_pathao(consignment_id: str, settings) -> dict:
    token = _get_pathao_token(settings)
    try:
        resp = httpx.get(
            f"{settings.pathao_base_url}/aladdin/api/v1/orders/{consignment_id}/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_COURIER_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Pathao tracking unavailable") from exc


# ---------------------------------------------------------------------------
# Steadfast helpers
# ---------------------------------------------------------------------------


def _steadfast_headers(settings) -> dict:
    return {
        "Api-Key": settings.steadfast_api_key or "",
        "Secret-Key": settings.steadfast_secret_key or "",
        "Content-Type": "application/json",
    }


def _create_steadfast_parcel(order: dict, dispatch: DispatchRequest, settings) -> dict:
    if not settings.steadfast_api_key or not settings.steadfast_secret_key:
        raise HTTPException(status_code=503, detail="Steadfast not configured")

    addr = order["delivery_address"]
    try:
        resp = httpx.post(
            f"{settings.steadfast_base_url}/create_order",
            headers=_steadfast_headers(settings),
            json={
                "invoice": order["reference"],
                "recipient_name": addr.get("name", "Customer"),
                "recipient_phone": addr.get("phone", "01700000000"),
                "recipient_address": (
                    f"{addr.get('line1', '')} {addr.get('district', '')}".strip()
                ),
                "cod_amount": (
                    float(order["total_amount"])
                    if order["payment_method"] == "cod"
                    else 0
                ),
                "note": dispatch.note or "",
                "weight": dispatch.item_weight_kg,
            },
            timeout=_COURIER_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Steadfast unavailable") from exc


def _track_steadfast(tracking_id: str, settings) -> dict:
    if not settings.steadfast_api_key:
        raise HTTPException(status_code=503, detail="Steadfast not configured")
    try:
        resp = httpx.get(
            f"{settings.steadfast_base_url}/status_by_tracking/{tracking_id}",
            headers=_steadfast_headers(settings),
            timeout=_COURIER_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Steadfast tracking unavailable") from exc


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _load_order_for_dispatch(conn, order_id: str) -> dict:
    row = conn.execute(
        """
        SELECT id, reference, status, payment_method, total_amount,
               delivery_address, fulfilment_type, user_id
        FROM orders WHERE id = %s
        """,
        (order_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "id": str(row[0]),
        "reference": row[1],
        "status": row[2],
        "payment_method": row[3],
        "total_amount": row[4],
        "delivery_address": row[5] or {},
        "fulfilment_type": row[6] or "delivery",
        "user_id": str(row[7]),
    }


def _publish_notification_event(event_type: str, payload: dict) -> None:
    settings = get_settings()
    try:
        publish_message(
            settings.notification_events_queue_name,
            json.dumps({"type": event_type, **payload}),
        )
    except Exception:  # noqa: BLE001
        pass  # best-effort; don't fail the request


# ---------------------------------------------------------------------------
# Dispatch endpoint (admin)
# ---------------------------------------------------------------------------


@admin_router.post("/dispatch/{order_id}")
def dispatch_order(
    order_id: str,
    payload: DispatchRequest,
    user=Depends(require_admin),
):
    """Create a courier parcel for a confirmed order and store the tracking number."""
    settings = get_settings()

    with get_connection() as conn:
        order = _load_order_for_dispatch(conn, order_id)

    if order["status"] not in ("confirmed", "packed"):
        raise HTTPException(
            status_code=422,
            detail="Order must be confirmed or packed before dispatch",
        )
    if order["fulfilment_type"] == "pickup":
        raise HTTPException(status_code=422, detail="Pickup orders are not dispatched via courier")

    if payload.courier == "pathao":
        result = _create_pathao_parcel(order, payload, settings)
        consignment = (
            result.get("data", {}).get("consignment_id")
            or result.get("consignment_id", "")
        )
        tracking = consignment
    else:
        result = _create_steadfast_parcel(order, payload, settings)
        consignment = (
            result.get("consignment", {}).get("tracking_code")
            or result.get("tracking_code", "")
        )
        tracking = consignment

    with get_connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO courier_dispatches
                  (order_id, courier, consignment_id, tracking_id, status, raw_response)
                VALUES (%s, %s, %s, %s, 'created', %s::jsonb)
                """,
                (order_id, payload.courier, consignment, tracking, json.dumps(result)),
            )
            conn.execute(
                """
                UPDATE orders
                SET tracking_number = %s, status = 'shipped', updated_at = NOW()
                WHERE id = %s
                """,
                (tracking, order_id),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, status, changed_by, note)
                VALUES (%s, 'shipped', %s, %s)
                """,
                (order_id, user["id"], f"Dispatched via {payload.courier}"),
            )

    _publish_notification_event(
        "order_shipped",
        {
            "order_id": order_id,
            "user_id": order["user_id"],
            "reference": order["reference"],
            "courier": payload.courier,
            "tracking_number": tracking,
        },
    )
    return {"courier": payload.courier, "tracking_id": tracking, "consignment_id": consignment}


# ---------------------------------------------------------------------------
# Live tracking
# ---------------------------------------------------------------------------


@router.get("/track/{order_id}")
def track_order(order_id: str, user=Depends(get_current_user)):
    """Fetch live tracking status from the courier for a user's order."""
    settings = get_settings()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT cd.courier, cd.consignment_id, cd.tracking_id, cd.status
            FROM courier_dispatches cd
            JOIN orders o ON o.id = cd.order_id
            WHERE cd.order_id = %s AND (o.user_id = %s OR %s IN (
              SELECT id FROM users WHERE role_id IN (
                SELECT id FROM roles WHERE name IN ('admin','staff')
              )
            ))
            ORDER BY cd.created_at DESC LIMIT 1
            """,
            (order_id, user["id"], user["id"]),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No dispatch record found")

    courier, consignment_id, tracking_id, local_status = row

    if courier == "pathao" and consignment_id:
        live = _track_pathao(consignment_id, settings)
    elif courier == "steadfast" and tracking_id:
        live = _track_steadfast(tracking_id, settings)
    else:
        live = {}

    return {
        "courier": courier,
        "tracking_id": tracking_id,
        "local_status": local_status,
        "live": live,
    }


# ---------------------------------------------------------------------------
# Webhooks — courier delivery status updates
# ---------------------------------------------------------------------------


def _handle_webhook_update(conn, tracking_id: str, courier_status: str) -> Optional[str]:
    """Map courier status → order status and update if appropriate."""
    status_map = {
        # Pathao statuses
        "Delivered": "delivered",
        "Partial_Delivery": "delivered",
        "Return": "returned",
        "Return_Receive": "returned",
        # Steadfast statuses
        "delivered": "delivered",
        "partial_delivered": "delivered",
        "cancelled": "cancelled",
    }
    order_status = status_map.get(courier_status)
    if not order_status:
        return None

    row = conn.execute(
        """
        SELECT o.id, o.user_id, o.reference
        FROM courier_dispatches cd
        JOIN orders o ON o.id = cd.order_id
        WHERE cd.tracking_id = %s
        ORDER BY cd.created_at DESC LIMIT 1
        """,
        (tracking_id,),
    ).fetchone()

    if not row:
        return None

    order_id, user_id, reference = str(row[0]), str(row[1]), row[2]

    with conn.transaction():
        conn.execute(
            """
            UPDATE courier_dispatches SET status = %s, updated_at = NOW()
            WHERE tracking_id = %s
            """,
            (courier_status, tracking_id),
        )
        conn.execute(
            "UPDATE orders SET status = %s, updated_at = NOW() WHERE id = %s",
            (order_status, order_id),
        )
        conn.execute(
            """
            INSERT INTO order_status_history (order_id, status, note)
            VALUES (%s, %s, %s)
            """,
            (order_id, order_status, f"Courier webhook: {courier_status}"),
        )

    if order_status == "delivered":
        _publish_notification_event(
            "order_delivered",
            {"order_id": order_id, "user_id": user_id, "reference": reference},
        )
    return order_id


@router.post("/webhook/pathao")
async def pathao_webhook(request: Request):
    """Receive delivery status updates from Pathao."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {"status": "ignored"}

    # Pathao sends consignment_id and order_status in the webhook body
    consignment_id = data.get("consignment_id", "")
    courier_status = data.get("order_status", "")
    if not consignment_id or not courier_status:
        return {"status": "ignored"}

    with get_connection() as conn:
        _handle_webhook_update(conn, consignment_id, courier_status)

    return {"status": "ok"}


@router.post("/webhook/steadfast")
async def steadfast_webhook(request: Request):
    """Receive delivery status updates from Steadfast."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {"status": "ignored"}

    tracking_code = data.get("tracking_code", "")
    delivery_status = data.get("delivery_status", "")
    if not tracking_code or not delivery_status:
        return {"status": "ignored"}

    with get_connection() as conn:
        _handle_webhook_update(conn, tracking_code, delivery_status)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Shipping zones
# ---------------------------------------------------------------------------


@router.get("/zones")
def list_zones():
    """List all active shipping zones with their rates."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, districts, base_rate, rate_per_kg
            FROM delivery_zones WHERE is_active ORDER BY name
            """,
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "districts": r[2],
            "base_rate": str(r[3]),
            "rate_per_kg": str(r[4]),
        }
        for r in rows
    ]


@router.get("/zones/calculate")
def calculate_shipping(district: str, weight_kg: float = 0.5):
    """Calculate shipping cost for a destination district and parcel weight."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT base_rate, rate_per_kg
            FROM delivery_zones
            WHERE %s = ANY(districts) AND is_active
            LIMIT 1
            """,
            (district,),
        ).fetchone()

    if not row:
        return {"district": district, "shipping_cost": None, "message": "Zone not found"}

    cost = row[0] + (row[1] * Decimal(str(weight_kg)))
    return {"district": district, "weight_kg": weight_kg, "shipping_cost": str(cost)}


@admin_router.post("/zones")
def create_zone(payload: ZoneCreateRequest, user=Depends(require_admin)):
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO delivery_zones (name, districts, base_rate, rate_per_kg)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (payload.name, payload.districts, payload.base_rate, payload.rate_per_kg),
        ).fetchone()
    return {"id": str(row[0])}


@admin_router.patch("/zones/{zone_id}")
def update_zone(
    zone_id: str,
    payload: ZoneUpdateRequest,
    user=Depends(require_admin),
):
    fields, params = [], []
    if payload.name is not None:
        fields.append("name = %s"); params.append(payload.name)
    if payload.districts is not None:
        fields.append("districts = %s"); params.append(payload.districts)
    if payload.base_rate is not None:
        fields.append("base_rate = %s"); params.append(payload.base_rate)
    if payload.rate_per_kg is not None:
        fields.append("rate_per_kg = %s"); params.append(payload.rate_per_kg)
    if payload.is_active is not None:
        fields.append("is_active = %s"); params.append(payload.is_active)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")

    params.extend(["NOW()", zone_id])
    with get_connection() as conn:
        conn.execute(
            f"UPDATE delivery_zones SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s",
            params[:-1] + [zone_id],
        )
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Branches (public listing + admin management)
# ---------------------------------------------------------------------------


@branches_router.get("")
def list_branches():
    """List all active branches with address and opening hours."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, code, address, city, phone,
                   opening_hours, is_pickup_available
            FROM branches
            WHERE is_active = TRUE
            ORDER BY name
            """,
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "code": r[2],
            "address": r[3],
            "city": r[4],
            "phone": r[5],
            "opening_hours": r[6],
            "is_pickup_available": r[7],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Click & Collect — Milestone 5.2
# ---------------------------------------------------------------------------


@admin_router.patch("/pickup/{order_id}/ready")
def mark_pickup_ready(order_id: str, user=Depends(require_admin)):
    """Notify customer that their Click & Collect order is ready for pickup."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, fulfilment_type, status, user_id, reference, pickup_branch_id
            FROM orders WHERE id = %s
            """,
            (order_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        if row[1] != "pickup":
            raise HTTPException(status_code=422, detail="Order is not a pickup order")
        if row[2] not in ("confirmed", "packed"):
            raise HTTPException(
                status_code=422, detail="Order must be confirmed or packed"
            )

        # Load branch name for the notification message
        branch_row = conn.execute(
            "SELECT name FROM branches WHERE id = %s", (row[5],)
        ).fetchone()
        branch_name = branch_row[0] if branch_row else "our branch"

        with conn.transaction():
            conn.execute(
                """
                UPDATE orders
                SET status = 'packed', pickup_ready_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (order_id,),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, status, changed_by, note)
                VALUES (%s, 'packed', %s, 'Ready for customer pickup')
                """,
                (order_id, user["id"]),
            )

    _publish_notification_event(
        "pickup_ready",
        {
            "order_id": order_id,
            "user_id": str(row[3]),
            "reference": row[4],
            "branch_name": branch_name,
        },
    )
    return {"status": "ready_for_pickup"}


@admin_router.patch("/pickup/{order_id}/confirm")
def confirm_pickup(order_id: str, user=Depends(require_admin)):
    """Mark a Click & Collect order as collected by the customer."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, fulfilment_type, status FROM orders WHERE id = %s",
            (order_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        if row[1] != "pickup":
            raise HTTPException(status_code=422, detail="Order is not a pickup order")
        if row[2] != "packed":
            raise HTTPException(
                status_code=422, detail="Order must be in 'packed/ready' state"
            )

        with conn.transaction():
            conn.execute(
                """
                UPDATE orders
                SET status = 'delivered',
                    pickup_confirmed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (order_id,),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, status, changed_by, note)
                VALUES (%s, 'delivered', %s, 'Customer collected at branch')
                """,
                (order_id, user["id"]),
            )

    return {"status": "collected"}


@admin_router.get("/pickup/queue")
def pickup_queue(
    branch_id: Optional[str] = None,
    user=Depends(require_admin),
):
    """List pickup orders that are ready for collection at a branch."""
    conditions = ["o.fulfilment_type = 'pickup'", "o.status IN ('confirmed','packed')"]
    params: list = []
    if branch_id:
        conditions.append("o.pickup_branch_id = %s")
        params.append(branch_id)

    where = " AND ".join(conditions)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT o.id, o.reference, o.status, o.total_amount,
                   o.pickup_ready_at, b.name AS branch_name,
                   u.name AS customer_name, u.phone AS customer_phone
            FROM orders o
            LEFT JOIN branches b ON b.id = o.pickup_branch_id
            JOIN users u ON u.id = o.user_id
            WHERE {where}
            ORDER BY o.created_at DESC
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "reference": r[1],
            "status": r[2],
            "total_amount": str(r[3]),
            "pickup_ready_at": r[4].isoformat() if r[4] else None,
            "branch_name": r[5],
            "customer_name": r[6],
            "customer_phone": r[7],
        }
        for r in rows
    ]
