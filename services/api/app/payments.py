"""Phase 4 — Payment Integration.

Milestones:
  4.1 SSLCOMMERZ (credit/debit cards via IPN + server-side validation)
  4.2 bKash tokenized checkout
  4.3 Nagad RSA-encrypted checkout
  4.4 Cash on Delivery (COD) + admin payment reconciliation
"""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
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

router = APIRouter(prefix="/payments", tags=["payments"])
admin_router = APIRouter(prefix="/admin/payments", tags=["payments"])

_BKASH_TOKEN_REDIS_KEY = "bkash:access_token"
_GATEWAY_TIMEOUT = 15.0  # seconds for all external gateway calls
_ALL_GATEWAYS = ("sslcommerz", "bkash", "nagad", "cod")


# ---------------------------------------------------------------------------
# Gateway availability — driven entirely by env var presence
# ---------------------------------------------------------------------------


def _available_gateways(settings) -> list[str]:
    """Return gateways that have all required credentials set in the environment.

    COD requires no external keys and is always included.
    Every other gateway is included only when every required key is non-empty.
    """
    available = ["cod"]
    if settings.sslcommerz_store_id and settings.sslcommerz_store_pass:
        available.append("sslcommerz")
    if all(
        [
            settings.bkash_username,
            settings.bkash_password,
            settings.bkash_app_key,
            settings.bkash_app_secret,
        ]
    ):
        available.append("bkash")
    if all(
        [
            settings.nagad_merchant_id,
            settings.nagad_merchant_private_key,
            settings.nagad_public_key,
        ]
    ):
        available.append("nagad")
    return available


@router.get("/methods")
def list_payment_methods():
    """Return which payment gateways are available based on configured credentials.

    Gateways whose API keys are absent from the environment are listed under
    ``unavailable`` — only COD will be present in ``available`` in that case.
    """
    settings = get_settings()
    available = _available_gateways(settings)
    unavailable = [g for g in _ALL_GATEWAYS if g not in available]
    return {
        "available": available,
        "unavailable": unavailable,
        "cod_max_order_amount": settings.cod_max_order_amount,
    }


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class SSLCommerzInitiateRequest(BaseModel):
    order_id: str


class BKashCreateRequest(BaseModel):
    order_id: str
    payer_reference: Optional[str] = Field(None, max_length=50)


class BKashExecuteRequest(BaseModel):
    payment_id: str  # bKash paymentID returned from /bkash/create


class BKashRefundRequest(BaseModel):
    payment_id: str  # our DB payments.id
    reason: Optional[str] = Field(None, max_length=200)


class NagadInitiateRequest(BaseModel):
    order_id: str


class RetryPaymentRequest(BaseModel):
    gateway: str = Field(..., pattern=r"^(sslcommerz|bkash|nagad)$")


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------


def _get_order(conn, order_id: str, user_id: Optional[str] = None) -> dict:
    """Load an order row, optionally asserting ownership."""
    sql = """
        SELECT id, reference, total_amount, payment_status, payment_method, user_id
        FROM orders WHERE id = %s
    """
    params: list = [order_id]
    if user_id:
        sql += " AND user_id = %s"
        params.append(user_id)

    row = conn.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "id": str(row[0]),
        "reference": row[1],
        "total_amount": row[2],
        "payment_status": row[3],
        "payment_method": row[4],
        "user_id": str(row[5]),
    }


def _create_payment_record(
    conn,
    *,
    order_id: str,
    gateway: str,
    gateway_session_id: Optional[str],
    amount: Decimal,
    idempotency_key: str,
    status: str = "initiated",
) -> str:
    """Insert a payment row; returns the new payment UUID."""
    row = conn.execute(
        """
        INSERT INTO payments
          (order_id, gateway, gateway_session_id, amount, status, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (order_id, gateway, gateway_session_id, amount, status, idempotency_key),
    ).fetchone()
    return str(row[0])


def _update_payment_record(
    conn,
    *,
    payment_id: str,
    status: str,
    gateway_ref: Optional[str] = None,
    raw_response: Optional[dict] = None,
) -> None:
    conn.execute(
        """
        UPDATE payments
        SET status       = %s,
            gateway_ref  = COALESCE(%s, gateway_ref),
            raw_response = COALESCE(%s::jsonb, raw_response),
            updated_at   = NOW()
        WHERE id = %s
        """,
        (
            status,
            gateway_ref,
            json.dumps(raw_response) if raw_response is not None else None,
            payment_id,
        ),
    )


def _confirm_order_payment(conn, order_id: str, payment_ref: str) -> None:
    """Mark the order paid and advance its status to confirmed if still placed."""
    conn.execute(
        """
        UPDATE orders
        SET payment_status = 'paid',
            status         = CASE WHEN status = 'placed' THEN 'confirmed' ELSE status END,
            payment_ref    = %s,
            updated_at     = NOW()
        WHERE id = %s
        """,
        (payment_ref, order_id),
    )
    conn.execute(
        """
        INSERT INTO order_status_history (order_id, status, note)
        VALUES (%s, 'confirmed', 'Payment received')
        """,
        (order_id,),
    )


def _fail_order_payment(conn, order_id: str) -> None:
    conn.execute(
        "UPDATE orders SET payment_status = 'failed', updated_at = NOW() WHERE id = %s",
        (order_id,),
    )


def _publish_payment_event(
    event_type: str, order_id: str, gateway: str, gateway_ref: Optional[str]
) -> None:
    """Publish payment.confirmed or payment.failed to the message queue (best-effort)."""
    settings = get_settings()
    queue = (
        settings.payment_confirmed_queue_name
        if event_type == "confirmed"
        else settings.payment_failed_queue_name
    )
    try:
        publish_message(
            queue,
            json.dumps(
                {
                    "event": f"payment.{event_type}",
                    "order_id": order_id,
                    "gateway": gateway,
                    "gateway_ref": gateway_ref,
                }
            ),
        )
    except Exception:  # noqa: BLE001 – queue failures must not abort the response
        pass

    # On payment confirmed, also trigger a customer notification.
    if event_type == "confirmed":
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT reference, user_id, total_amount FROM orders WHERE id = %s",
                    (order_id,),
                ).fetchone()
            if row:
                publish_message(
                    settings.notification_events_queue_name,
                    json.dumps({
                        "type": "order_confirmed",
                        "order_id": order_id,
                        "reference": row[0],
                        "user_id": str(row[1]),
                        "total_amount": str(row[2]),
                    }),
                )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# SSLCOMMERZ — Milestone 4.1
# ---------------------------------------------------------------------------


def _ssl_api_url(is_sandbox: bool, path: str) -> str:
    host = "sandbox.sslcommerz.com" if is_sandbox else "securepay.sslcommerz.com"
    return f"https://{host}{path}"


def _verify_sslcommerz_hash(data: dict, store_pass: str) -> bool:
    """Verify SSLCOMMERZ IPN signature (MD5 hash chain per SSLCOMMERZ spec)."""
    verify_sign = data.get("verify_sign", "")
    verify_key = data.get("verify_key", "")
    if not verify_sign or not verify_key:
        return False

    keys = sorted(k.strip() for k in verify_key.split(","))
    pass_hash = hashlib.md5(store_pass.encode()).hexdigest()  # noqa: S324
    parts = [f"store_passwd={pass_hash}"] + [f"{k}={data.get(k, '')}" for k in keys]
    computed = hashlib.md5("&".join(parts).encode()).hexdigest()  # noqa: S324
    return hmac.compare_digest(computed, verify_sign)


def _do_sslcommerz_initiate(order: dict, user: dict, settings) -> dict:
    """Core SSLCOMMERZ session creation logic (shared by initiate + retry)."""
    tran_id = str(uuid.uuid4())
    public_base = settings.public_base_url

    form = {
        "store_id": settings.sslcommerz_store_id,
        "store_passwd": settings.sslcommerz_store_pass,
        "total_amount": str(order["total_amount"]),
        "currency": "BDT",
        "tran_id": tran_id,
        "success_url": f"{public_base}/api/v1/payments/sslcommerz/success",
        "fail_url": f"{public_base}/api/v1/payments/sslcommerz/fail",
        "cancel_url": f"{public_base}/api/v1/payments/sslcommerz/cancel",
        "ipn_url": f"{public_base}/api/v1/payments/sslcommerz/success",
        "cus_name": user.get("name", "Customer"),
        "cus_email": user.get("email", "customer@example.com"),
        "cus_phone": user.get("phone", "01700000000"),
        "cus_add1": "N/A",
        "cus_city": "Dhaka",
        "cus_country": "Bangladesh",
        "shipping_method": "NO",
        "product_name": f"Order {order['reference']}",
        "product_category": "General",
        "product_profile": "general",
        "value_a": order["id"],  # passed through callback for order lookup
    }

    try:
        resp = httpx.post(
            _ssl_api_url(settings.sslcommerz_is_sandbox, "/gwprocess/v4/api.php"),
            data=form,
            timeout=_GATEWAY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Payment gateway unavailable") from exc

    if data.get("status") != "SUCCESS":
        raise HTTPException(
            status_code=502, detail=data.get("failedreason", "Gateway session creation failed")
        )

    with get_connection() as conn:
        _create_payment_record(
            conn,
            order_id=order["id"],
            gateway="sslcommerz",
            gateway_session_id=tran_id,
            amount=order["total_amount"],
            idempotency_key=f"ssl:{tran_id}",
        )

    return {"gateway_url": data.get("GatewayPageURL"), "tran_id": tran_id}


@router.post("/sslcommerz/initiate")
def sslcommerz_initiate(
    payload: SSLCommerzInitiateRequest,
    user=Depends(get_current_user),
):
    """Create an SSLCOMMERZ payment session; return the hosted payment URL."""
    settings = get_settings()
    if not settings.sslcommerz_store_id or not settings.sslcommerz_store_pass:
        raise HTTPException(status_code=503, detail="SSLCOMMERZ not configured")

    with get_connection() as conn:
        order = _get_order(conn, payload.order_id, user["id"])

    if order["payment_status"] == "paid":
        raise HTTPException(status_code=409, detail="Order already paid")

    return _do_sslcommerz_initiate(order, user, settings)


@router.post("/sslcommerz/success")
async def sslcommerz_success(request: Request):
    """Handle SSLCOMMERZ success / IPN callback (form-data POST from gateway)."""
    settings = get_settings()
    if not settings.sslcommerz_store_pass:
        return {"status": "ignored"}

    form = await request.form()
    data = dict(form)

    if not _verify_sslcommerz_hash(data, settings.sslcommerz_store_pass):
        raise HTTPException(status_code=400, detail="Invalid IPN signature")

    tran_id = data.get("tran_id", "")
    order_id = data.get("value_a", "")
    val_id = data.get("val_id", "")
    cb_status = data.get("status", "")

    if not tran_id or not order_id:
        return {"status": "ignored"}

    idempotency_key = f"ssl:{tran_id}"

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, status FROM payments WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()

        if existing and existing[1] in ("paid", "failed"):
            return {"status": "already_processed"}

        payment_id = str(existing[0]) if existing else None

        if cb_status == "VALID":
            # Server-side validation with SSLCOMMERZ (double-verification)
            try:
                val_resp = httpx.get(
                    _ssl_api_url(
                        settings.sslcommerz_is_sandbox,
                        "/validator/api/validationserverAPI.php",
                    ),
                    params={
                        "val_id": val_id,
                        "store_id": settings.sslcommerz_store_id,
                        "store_passwd": settings.sslcommerz_store_pass,
                        "format": "json",
                    },
                    timeout=_GATEWAY_TIMEOUT,
                )
                val_data = val_resp.json()
            except Exception:  # noqa: BLE001
                val_data = data

            with conn.transaction():
                if payment_id:
                    _update_payment_record(
                        conn,
                        payment_id=payment_id,
                        status="paid",
                        gateway_ref=val_id,
                        raw_response=val_data,
                    )
                else:
                    _create_payment_record(
                        conn,
                        order_id=order_id,
                        gateway="sslcommerz",
                        gateway_session_id=tran_id,
                        amount=Decimal(data.get("amount", "0")),
                        idempotency_key=idempotency_key,
                        status="paid",
                    )
                _confirm_order_payment(conn, order_id, val_id or tran_id)

            _publish_payment_event("confirmed", order_id, "sslcommerz", val_id)
        else:
            with conn.transaction():
                if payment_id:
                    _update_payment_record(
                        conn,
                        payment_id=payment_id,
                        status="failed",
                        raw_response=data,
                    )
                _fail_order_payment(conn, order_id)

            _publish_payment_event("failed", order_id, "sslcommerz", None)

    return {"status": "ok"}


@router.post("/sslcommerz/fail")
async def sslcommerz_fail(request: Request):
    """Handle SSLCOMMERZ payment failure callback."""
    form = await request.form()
    data = dict(form)
    tran_id = data.get("tran_id", "")
    order_id = data.get("value_a", "")
    if not tran_id or not order_id:
        return {"status": "ignored"}

    idempotency_key = f"ssl:{tran_id}"
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, status FROM payments WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if existing and existing[1] == "paid":
            return {"status": "already_paid"}

        with conn.transaction():
            if existing:
                _update_payment_record(
                    conn,
                    payment_id=str(existing[0]),
                    status="failed",
                    raw_response=data,
                )
            _fail_order_payment(conn, order_id)

    _publish_payment_event("failed", order_id, "sslcommerz", None)
    return {"status": "ok"}


@router.post("/sslcommerz/cancel")
async def sslcommerz_cancel(request: Request):
    """Handle SSLCOMMERZ payment cancellation callback."""
    form = await request.form()
    data = dict(form)
    tran_id = data.get("tran_id", "")
    order_id = data.get("value_a", "")
    if not tran_id or not order_id:
        return {"status": "ignored"}

    idempotency_key = f"ssl:{tran_id}"
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, status FROM payments WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if existing and existing[1] == "paid":
            return {"status": "already_paid"}

        with conn.transaction():
            if existing:
                _update_payment_record(
                    conn,
                    payment_id=str(existing[0]),
                    status="cancelled",
                    raw_response=data,
                )
            _fail_order_payment(conn, order_id)

    _publish_payment_event("failed", order_id, "sslcommerz", None)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# bKash — Milestone 4.2
# ---------------------------------------------------------------------------


def _require_bkash(settings) -> None:
    if not all(
        [
            settings.bkash_base_url,
            settings.bkash_username,
            settings.bkash_password,
            settings.bkash_app_key,
            settings.bkash_app_secret,
        ]
    ):
        raise HTTPException(status_code=503, detail="bKash not configured")


def _bkash_auth_headers(settings, token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-App-Key": settings.bkash_app_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _get_bkash_token(settings) -> str:
    """Return a valid bKash id_token, fetching a fresh one when Redis cache misses."""
    redis = get_redis()
    cached = redis.get(_BKASH_TOKEN_REDIS_KEY)
    if cached:
        return cached.decode()

    try:
        resp = httpx.post(
            f"{settings.bkash_base_url}/tokenized/checkout/token/grant",
            headers={
                "username": settings.bkash_username,
                "password": settings.bkash_password,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"app_key": settings.bkash_app_key, "app_secret": settings.bkash_app_secret},
            timeout=_GATEWAY_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="bKash token grant failed") from exc

    token = body.get("id_token")
    if not token:
        raise HTTPException(status_code=502, detail="bKash token grant returned no token")

    ttl = max(int(body.get("expires_in", 3600)) - 60, 60)
    redis.setex(_BKASH_TOKEN_REDIS_KEY, ttl, token)
    return token


def _do_bkash_create(
    order: dict, user: dict, settings, payer_reference: Optional[str] = None
) -> dict:
    """Core bKash payment creation logic (shared by create + retry)."""
    token = _get_bkash_token(settings)
    merchant_invoice = f"INV-{order['reference']}-{uuid.uuid4().hex[:8]}"
    payer_ref = payer_reference or user.get("phone", "01700000000")

    try:
        resp = httpx.post(
            f"{settings.bkash_base_url}/tokenized/checkout/create",
            headers=_bkash_auth_headers(settings, token),
            json={
                "mode": "0011",
                "payerReference": payer_ref,
                "callbackURL": f"{settings.public_base_url}/api/v1/payments/bkash/callback",
                "amount": str(order["total_amount"]),
                "currency": "BDT",
                "intent": "sale",
                "merchantInvoiceNumber": merchant_invoice,
            },
            timeout=_GATEWAY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="bKash unavailable") from exc

    if data.get("statusCode") != "0000":
        raise HTTPException(
            status_code=502, detail=data.get("statusMessage", "bKash payment creation failed")
        )

    bkash_payment_id = data["paymentID"]
    with get_connection() as conn:
        _create_payment_record(
            conn,
            order_id=order["id"],
            gateway="bkash",
            gateway_session_id=bkash_payment_id,
            amount=order["total_amount"],
            idempotency_key=f"bkash:{bkash_payment_id}",
        )

    return {"bkash_url": data.get("bkashURL"), "payment_id": bkash_payment_id}


@router.post("/bkash/create")
def bkash_create(payload: BKashCreateRequest, user=Depends(get_current_user)):
    """Create a bKash tokenized payment and return the bKash redirect URL."""
    settings = get_settings()
    _require_bkash(settings)

    with get_connection() as conn:
        order = _get_order(conn, payload.order_id, user["id"])

    if order["payment_status"] == "paid":
        raise HTTPException(status_code=409, detail="Order already paid")

    return _do_bkash_create(order, user, settings, payload.payer_reference)


@router.post("/bkash/execute")
def bkash_execute(payload: BKashExecuteRequest, user=Depends(get_current_user)):
    """Execute a bKash payment after the user has approved it in the bKash app."""
    settings = get_settings()
    _require_bkash(settings)

    idempotency_key = f"bkash:{payload.payment_id}"

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, order_id, status FROM payments WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payment session not found")
        if row[2] == "paid":
            return {"status": "already_paid"}

        _get_order(conn, str(row[1]), user["id"])  # ownership check

    payment_id_db = str(row[0])
    order_id = str(row[1])

    token = _get_bkash_token(settings)
    try:
        resp = httpx.post(
            f"{settings.bkash_base_url}/tokenized/checkout/execute",
            headers=_bkash_auth_headers(settings, token),
            json={"paymentID": payload.payment_id},
            timeout=_GATEWAY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="bKash execute failed") from exc

    trx_status = data.get("transactionStatus", "")
    trx_id = data.get("trxID")

    with get_connection() as conn:
        with conn.transaction():
            if trx_status == "Completed":
                _update_payment_record(
                    conn,
                    payment_id=payment_id_db,
                    status="paid",
                    gateway_ref=trx_id,
                    raw_response=data,
                )
                _confirm_order_payment(conn, order_id, trx_id or payload.payment_id)
            else:
                _update_payment_record(
                    conn,
                    payment_id=payment_id_db,
                    status="failed",
                    raw_response=data,
                )
                _fail_order_payment(conn, order_id)

    event = "confirmed" if trx_status == "Completed" else "failed"
    _publish_payment_event(event, order_id, "bkash", trx_id)

    return {"status": trx_status, "trx_id": trx_id, "payment_id": payload.payment_id}


@router.get("/bkash/query/{payment_id}")
def bkash_query(payment_id: str, user=Depends(get_current_user)):
    """Query bKash for live payment status."""
    settings = get_settings()
    _require_bkash(settings)

    idempotency_key = f"bkash:{payment_id}"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, order_id FROM payments WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payment not found")
        _get_order(conn, str(row[1]), user["id"])  # ownership check

    token = _get_bkash_token(settings)
    try:
        resp = httpx.post(
            f"{settings.bkash_base_url}/tokenized/checkout/payment/status",
            headers=_bkash_auth_headers(settings, token),
            json={"paymentID": payment_id},
            timeout=_GATEWAY_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="bKash query failed") from exc


@router.post("/bkash/refund")
def bkash_refund(payload: BKashRefundRequest, user=Depends(get_current_user)):
    """Initiate a bKash refund for a previously paid payment."""
    settings = get_settings()
    _require_bkash(settings)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, order_id, gateway_session_id, gateway_ref, amount, status
            FROM payments WHERE id = %s
            """,
            (payload.payment_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payment not found")
        if row[5] != "paid":
            raise HTTPException(status_code=422, detail="Payment is not in a paid state")
        _get_order(conn, str(row[1]), user["id"])  # ownership check

    token = _get_bkash_token(settings)
    try:
        resp = httpx.post(
            f"{settings.bkash_base_url}/tokenized/checkout/refund",
            headers=_bkash_auth_headers(settings, token),
            json={
                "paymentID": str(row[2]),
                "amount": str(row[4]),
                "trxID": str(row[3]),
                "sku": "refund",
                "reason": payload.reason or "Return approved",
            },
            timeout=_GATEWAY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="bKash refund failed") from exc

    if data.get("statusCode") == "0000":
        with get_connection() as conn:
            _update_payment_record(
                conn,
                payment_id=payload.payment_id,
                status="refunded",
                raw_response=data,
            )
        return {"status": "refunded", "refund_trx_id": data.get("refundTrxID")}

    raise HTTPException(
        status_code=502, detail=data.get("statusMessage", "Refund failed")
    )


# ---------------------------------------------------------------------------
# Nagad — Milestone 4.3
# ---------------------------------------------------------------------------


def _require_nagad(settings) -> None:
    if not all(
        [
            settings.nagad_base_url,
            settings.nagad_merchant_id,
            settings.nagad_merchant_private_key,
            settings.nagad_public_key,
        ]
    ):
        raise HTTPException(status_code=503, detail="Nagad not configured")


def _nagad_sign(data: str, private_key_pem: str) -> str:
    """Sign a string with the merchant's RSA private key (SHA-256 + PKCS1v15)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(data.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _nagad_encrypt(data: str, public_key_pem: str) -> str:
    """Encrypt a string with Nagad's RSA public key (OAEP + SHA-1)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_public_key(public_key_pem.encode())
    enc = key.encrypt(
        data.encode(),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),  # noqa: S303 — Nagad requires SHA-1
            algorithm=hashes.SHA1(),  # noqa: S303
            label=None,
        ),
    )
    return base64.b64encode(enc).decode()


def _do_nagad_initiate(order: dict, user: dict, settings, order_id_str: str) -> dict:
    """Core Nagad initiation logic (shared by initiate + retry)."""
    datetime_str = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    challenge = uuid.uuid4().hex

    sensitive_payload = json.dumps(
        {
            "merchantId": settings.nagad_merchant_id,
            "datetime": datetime_str,
            "orderId": order_id_str,
            "challenge": challenge,
        }
    )

    try:
        sensitive_data = _nagad_encrypt(sensitive_payload, settings.nagad_public_key)
        signature = _nagad_sign(sensitive_payload, settings.nagad_merchant_private_key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Nagad RSA key error") from exc

    nagad_headers = {
        "Content-Type": "application/json",
        "X-KM-Api-Version": "v-0.2.0",
        "X-KM-IP-V4": "127.0.0.1",
        "X-KM-Client-Type": "PC_WEB",
    }

    try:
        init_resp = httpx.post(
            f"{settings.nagad_base_url}/api/dfs/check-out/initialize"
            f"/{settings.nagad_merchant_id}/{order_id_str}",
            json={
                "DateTime": datetime_str,
                "SensitiveData": sensitive_data,
                "Signature": signature,
            },
            headers=nagad_headers,
            timeout=_GATEWAY_TIMEOUT,
        )
        init_resp.raise_for_status()
        init_data = init_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Nagad unavailable") from exc

    if init_data.get("status") != "Success":
        raise HTTPException(
            status_code=502, detail=init_data.get("reason", "Nagad initialization failed")
        )

    payment_ref_id = init_data.get("paymentReferenceId")
    callback_url = f"{settings.public_base_url}/api/v1/payments/nagad/callback"

    complete_payload = json.dumps(
        {
            "merchantId": settings.nagad_merchant_id,
            "orderId": order_id_str,
            "amount": str(order["total_amount"]),
            "currencyCode": "050",
            "challenge": challenge,
            "orderDateTime": datetime_str,
            "customerMobileNo": user.get("phone", "01700000000"),
            "callbackURL": callback_url,
            "additionalMerchantInfo": {
                "Name": "Amazra",
                "Designation": "NA",
                "Address": "NA",
                "DeviceID": "NA",
                "Organization": "Amazra",
            },
        }
    )

    try:
        complete_sensitive = _nagad_encrypt(complete_payload, settings.nagad_public_key)
        complete_sig = _nagad_sign(complete_payload, settings.nagad_merchant_private_key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Nagad RSA key error") from exc

    try:
        complete_resp = httpx.post(
            f"{settings.nagad_base_url}/api/dfs/check-out/complete/{payment_ref_id}",
            json={
                "DateTime": datetime_str,
                "SensitiveData": complete_sensitive,
                "Signature": complete_sig,
            },
            headers=nagad_headers,
            timeout=_GATEWAY_TIMEOUT,
        )
        complete_resp.raise_for_status()
        complete_data = complete_resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Nagad complete step failed") from exc

    idempotency_key = f"nagad:{payment_ref_id}"
    with get_connection() as conn:
        _create_payment_record(
            conn,
            order_id=order["id"],
            gateway="nagad",
            gateway_session_id=payment_ref_id,
            amount=order["total_amount"],
            idempotency_key=idempotency_key,
        )

    return {
        "redirect_url": complete_data.get("callBackUrl"),
        "payment_ref_id": payment_ref_id,
    }


@router.post("/nagad/initiate")
def nagad_initiate(payload: NagadInitiateRequest, user=Depends(get_current_user)):
    """Initiate a Nagad payment; returns a redirect URL to the Nagad checkout."""
    settings = get_settings()
    _require_nagad(settings)

    with get_connection() as conn:
        order = _get_order(conn, payload.order_id, user["id"])

    if order["payment_status"] == "paid":
        raise HTTPException(status_code=409, detail="Order already paid")

    return _do_nagad_initiate(order, user, settings, order["reference"])


@router.post("/nagad/callback")
async def nagad_callback(request: Request):
    """Handle Nagad payment result callback (JSON POST from Nagad)."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}

    payment_ref_id = data.get("payment_ref_id") or data.get("paymentRefId", "")
    status = data.get("status", "")

    if not payment_ref_id:
        return {"status": "ignored"}

    idempotency_key = f"nagad:{payment_ref_id}"

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, order_id, status FROM payments WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        if not row:
            return {"status": "ignored"}
        if row[2] in ("paid", "failed"):
            return {"status": "already_processed"}

        payment_id = str(row[0])
        order_id = str(row[1])

        with conn.transaction():
            if status in ("Success", "Paid"):
                _update_payment_record(
                    conn,
                    payment_id=payment_id,
                    status="paid",
                    gateway_ref=payment_ref_id,
                    raw_response=data,
                )
                _confirm_order_payment(conn, order_id, payment_ref_id)
            else:
                _update_payment_record(
                    conn,
                    payment_id=payment_id,
                    status="failed",
                    raw_response=data,
                )
                _fail_order_payment(conn, order_id)

    event = "confirmed" if status in ("Success", "Paid") else "failed"
    _publish_payment_event(event, order_id, "nagad", payment_ref_id if event == "confirmed" else None)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# COD — admin confirms cash received on delivery (Milestone 4.4)
# ---------------------------------------------------------------------------


@admin_router.post("/cod/{order_id}/confirm")
def cod_confirm_payment(order_id: str, user=Depends(require_admin)):
    """Mark a COD order's cash payment as received after delivery confirmation."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, payment_method, payment_status, total_amount, reference FROM orders WHERE id = %s",
            (order_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        if row[1] != "cod":
            raise HTTPException(status_code=422, detail="Order payment method is not COD")
        if row[2] == "paid":
            raise HTTPException(status_code=409, detail="Payment already confirmed")

        with conn.transaction():
            conn.execute(
                "UPDATE orders SET payment_status = 'paid', updated_at = NOW() WHERE id = %s",
                (order_id,),
            )
            # Insert or ignore if already exists (idempotent)
            conn.execute(
                """
                INSERT INTO payments
                  (order_id, gateway, gateway_session_id, amount, status, idempotency_key)
                VALUES (%s, 'cod', %s, %s, 'paid', %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (order_id, row[4], row[3], f"cod:{order_id}"),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, status, changed_by, note)
                VALUES (%s, 'confirmed', %s, 'COD payment received at delivery')
                """,
                (order_id, user["id"]),
            )

    _publish_payment_event("confirmed", order_id, "cod", None)
    return {"status": "paid"}


# ---------------------------------------------------------------------------
# Retry — create a new payment session for a failed/pending order
# ---------------------------------------------------------------------------


@router.post("/{order_id}/retry")
def retry_payment(
    order_id: str,
    payload: RetryPaymentRequest,
    user=Depends(get_current_user),
):
    """Generate a new gateway payment session for an order whose payment failed."""
    with get_connection() as conn:
        order = _get_order(conn, order_id, user["id"])

    if order["payment_status"] == "paid":
        raise HTTPException(status_code=409, detail="Order already paid")
    if order["payment_method"] == "cod":
        raise HTTPException(status_code=422, detail="COD orders do not use gateway payments")

    settings = get_settings()
    gateway = payload.gateway

    if gateway == "sslcommerz":
        if not settings.sslcommerz_store_id or not settings.sslcommerz_store_pass:
            raise HTTPException(status_code=503, detail="SSLCOMMERZ not configured")
        return _do_sslcommerz_initiate(order, user, settings)

    if gateway == "bkash":
        _require_bkash(settings)
        return _do_bkash_create(order, user, settings)

    if gateway == "nagad":
        _require_nagad(settings)
        # append a short suffix so the orderId is unique for this retry attempt
        retry_order_id_str = f"{order['reference']}-{uuid.uuid4().hex[:6]}"
        return _do_nagad_initiate(order, user, settings, retry_order_id_str)

    raise HTTPException(status_code=400, detail="Unsupported gateway")


# ---------------------------------------------------------------------------
# Admin — list payments + reconciliation (Milestone 4.4)
# ---------------------------------------------------------------------------


@admin_router.get("")
def list_payments(
    gateway: Optional[str] = None,
    status: Optional[str] = None,
    order_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    per_page: int = 24,
    user=Depends(require_admin),
):
    """List all payment records with optional filters."""
    offset = max(page - 1, 0) * per_page
    conditions: list[str] = []
    params: list = []

    if gateway:
        conditions.append("gateway = %s")
        params.append(gateway)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if order_id:
        conditions.append("order_id = %s")
        params.append(order_id)
    if start_date:
        conditions.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("created_at <= %s")
        params.append(end_date)

    where = " AND ".join(conditions) or "TRUE"
    params.extend([per_page, offset])

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, order_id, gateway, gateway_session_id, gateway_ref,
                   amount, status, created_at
            FROM payments
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "order_id": str(r[1]),
            "gateway": r[2],
            "gateway_session_id": r[3],
            "gateway_ref": r[4],
            "amount": str(r[5]),
            "status": r[6],
            "created_at": r[7].isoformat(),
        }
        for r in rows
    ]


@admin_router.get("/reconciliation")
def reconciliation_report(
    date: Optional[str] = None,
    user=Depends(require_admin),
):
    """Daily reconciliation: payments table vs orders table, grouped by gateway/status."""
    report_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_connection() as conn:
        payment_rows = conn.execute(
            """
            SELECT gateway, status,
                   COUNT(*)::int   AS tx_count,
                   SUM(amount)     AS total_amount
            FROM payments
            WHERE created_at::date = %s::date
            GROUP BY gateway, status
            ORDER BY gateway, status
            """,
            (report_date,),
        ).fetchall()

        order_rows = conn.execute(
            """
            SELECT payment_method, payment_status,
                   COUNT(*)::int       AS order_count,
                   SUM(total_amount)   AS total_amount
            FROM orders
            WHERE created_at::date = %s::date
            GROUP BY payment_method, payment_status
            ORDER BY payment_method
            """,
            (report_date,),
        ).fetchall()

    return {
        "date": report_date,
        "gateway_summary": [
            {
                "gateway": r[0],
                "status": r[1],
                "transaction_count": r[2],
                "total_amount": str(r[3]),
            }
            for r in payment_rows
        ],
        "order_summary": [
            {
                "payment_method": r[0],
                "payment_status": r[1],
                "order_count": r[2],
                "total_amount": str(r[3]),
            }
            for r in order_rows
        ],
    }
