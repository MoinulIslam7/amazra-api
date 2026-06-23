"""Phase 5.3 + 5.4 — Notification Preferences, Price Alerts, Restock Alerts.

Covers:
  - GET/PATCH /notifications/preferences
  - POST/DELETE /notifications/price-alert   (max 20 per user)
  - POST/DELETE /notifications/restock-alert (max 20 per user)
  - GET  /notifications/my-alerts
  - DELETE /notifications/alerts/{alert_id}
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .db import get_connection
from .deps import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

_MAX_ALERTS_PER_USER = 20


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class PreferencesUpdateRequest(BaseModel):
    sms_order_updates: Optional[bool] = None
    email_order_updates: Optional[bool] = None
    sms_marketing: Optional[bool] = None
    email_marketing: Optional[bool] = None


class PriceAlertRequest(BaseModel):
    product_id: str
    target_price: float = Field(..., gt=0)


class RestockAlertRequest(BaseModel):
    product_id: str


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@router.get("/preferences")
def get_preferences(user=Depends(get_current_user)):
    """Return the current user's notification channel preferences."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT sms_order_updates, email_order_updates,
                   sms_marketing, email_marketing, updated_at
            FROM notification_preferences
            WHERE user_id = %s
            """,
            (user["id"],),
        ).fetchone()

    if not row:
        return {
            "sms_order_updates": True,
            "email_order_updates": True,
            "sms_marketing": False,
            "email_marketing": False,
        }

    return {
        "sms_order_updates": row[0],
        "email_order_updates": row[1],
        "sms_marketing": row[2],
        "email_marketing": row[3],
        "updated_at": row[4].isoformat() if row[4] else None,
    }


@router.patch("/preferences")
def update_preferences(
    payload: PreferencesUpdateRequest,
    user=Depends(get_current_user),
):
    """Upsert notification preferences for the current user."""
    if all(v is None for v in payload.model_dump().values()):
        raise HTTPException(status_code=422, detail="No fields to update")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO notification_preferences
              (user_id, sms_order_updates, email_order_updates,
               sms_marketing, email_marketing)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
              sms_order_updates   = COALESCE(EXCLUDED.sms_order_updates,
                                             notification_preferences.sms_order_updates),
              email_order_updates = COALESCE(EXCLUDED.email_order_updates,
                                             notification_preferences.email_order_updates),
              sms_marketing       = COALESCE(EXCLUDED.sms_marketing,
                                             notification_preferences.sms_marketing),
              email_marketing     = COALESCE(EXCLUDED.email_marketing,
                                             notification_preferences.email_marketing),
              updated_at          = NOW()
            """,
            (
                user["id"],
                payload.sms_order_updates,
                payload.email_order_updates,
                payload.sms_marketing,
                payload.email_marketing,
            ),
        )
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Price alerts
# ---------------------------------------------------------------------------


def _count_active_alerts(conn, user_id: str) -> int:
    prices = conn.execute(
        "SELECT COUNT(*) FROM price_alerts WHERE user_id = %s AND is_active",
        (user_id,),
    ).fetchone()[0]
    restocks = conn.execute(
        "SELECT COUNT(*) FROM restock_alerts WHERE user_id = %s AND is_active",
        (user_id,),
    ).fetchone()[0]
    return int(prices) + int(restocks)


@router.post("/price-alert", status_code=201)
def create_price_alert(
    payload: PriceAlertRequest,
    user=Depends(get_current_user),
):
    """Subscribe to a price-drop alert for a product."""
    with get_connection() as conn:
        prod = conn.execute(
            "SELECT id, name, price FROM products WHERE id = %s",
            (payload.product_id,),
        ).fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")

        if _count_active_alerts(conn, user["id"]) >= _MAX_ALERTS_PER_USER:
            raise HTTPException(
                status_code=422,
                detail=f"You can have at most {_MAX_ALERTS_PER_USER} active alerts",
            )

        conn.execute(
            """
            INSERT INTO price_alerts (user_id, product_id, target_price)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, product_id) DO UPDATE
              SET target_price = EXCLUDED.target_price,
                  is_active    = TRUE,
                  triggered_at = NULL
            """,
            (user["id"], payload.product_id, payload.target_price),
        )

    return {
        "status": "created",
        "product_name": prod[1],
        "current_price": str(prod[2]),
        "target_price": payload.target_price,
    }


@router.delete("/price-alert/{product_id}", status_code=200)
def delete_price_alert(product_id: str, user=Depends(get_current_user)):
    """Cancel a price-drop alert."""
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE price_alerts SET is_active = FALSE
            WHERE user_id = %s AND product_id = %s AND is_active
            RETURNING id
            """,
            (user["id"], product_id),
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Restock alerts
# ---------------------------------------------------------------------------


@router.post("/restock-alert", status_code=201)
def create_restock_alert(
    payload: RestockAlertRequest,
    user=Depends(get_current_user),
):
    """Subscribe to a back-in-stock alert for a product."""
    with get_connection() as conn:
        prod = conn.execute(
            "SELECT id, name FROM products WHERE id = %s",
            (payload.product_id,),
        ).fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")

        if _count_active_alerts(conn, user["id"]) >= _MAX_ALERTS_PER_USER:
            raise HTTPException(
                status_code=422,
                detail=f"You can have at most {_MAX_ALERTS_PER_USER} active alerts",
            )

        conn.execute(
            """
            INSERT INTO restock_alerts (user_id, product_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, product_id) DO UPDATE
              SET is_active    = TRUE,
                  triggered_at = NULL
            """,
            (user["id"], payload.product_id),
        )

    return {"status": "created", "product_name": prod[1]}


@router.delete("/restock-alert/{product_id}", status_code=200)
def delete_restock_alert(product_id: str, user=Depends(get_current_user)):
    """Cancel a restock alert."""
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE restock_alerts SET is_active = FALSE
            WHERE user_id = %s AND product_id = %s AND is_active
            RETURNING id
            """,
            (user["id"], product_id),
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Combined alert listing + generic delete
# ---------------------------------------------------------------------------


@router.get("/my-alerts")
def list_my_alerts(user=Depends(get_current_user)):
    """Return all active price and restock alerts for the current user."""
    with get_connection() as conn:
        price_rows = conn.execute(
            """
            SELECT pa.id, p.id AS product_id, p.name, p.price,
                   pa.target_price, pa.created_at
            FROM price_alerts pa
            JOIN products p ON p.id = pa.product_id
            WHERE pa.user_id = %s AND pa.is_active
            ORDER BY pa.created_at DESC
            """,
            (user["id"],),
        ).fetchall()

        restock_rows = conn.execute(
            """
            SELECT ra.id, p.id AS product_id, p.name, ra.created_at
            FROM restock_alerts ra
            JOIN products p ON p.id = ra.product_id
            WHERE ra.user_id = %s AND ra.is_active
            ORDER BY ra.created_at DESC
            """,
            (user["id"],),
        ).fetchall()

    return {
        "price_alerts": [
            {
                "id": str(r[0]),
                "product_id": str(r[1]),
                "product_name": r[2],
                "current_price": str(r[3]),
                "target_price": str(r[4]),
                "created_at": r[5].isoformat(),
            }
            for r in price_rows
        ],
        "restock_alerts": [
            {
                "id": str(r[0]),
                "product_id": str(r[1]),
                "product_name": r[2],
                "created_at": r[3].isoformat(),
            }
            for r in restock_rows
        ],
    }


@router.delete("/alerts/{alert_id}")
def delete_alert_by_id(alert_id: str, user=Depends(get_current_user)):
    """Cancel any alert (price or restock) by its UUID."""
    with get_connection() as conn:
        price = conn.execute(
            """
            UPDATE price_alerts SET is_active = FALSE
            WHERE id = %s AND user_id = %s AND is_active RETURNING id
            """,
            (alert_id, user["id"]),
        ).fetchone()
        if price:
            return {"status": "cancelled", "type": "price_alert"}

        restock = conn.execute(
            """
            UPDATE restock_alerts SET is_active = FALSE
            WHERE id = %s AND user_id = %s AND is_active RETURNING id
            """,
            (alert_id, user["id"]),
        ).fetchone()
        if restock:
            return {"status": "cancelled", "type": "restock_alert"}

    raise HTTPException(status_code=404, detail="Alert not found")
