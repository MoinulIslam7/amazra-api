"""Phase 5.3 — Notification Worker.

Consumes notification_events queue and dispatches SMS + email via:
  - SMS: Twilio or generic HTTP (configurable via SMS_PROVIDER env var)
  - Email: SendGrid

Retry logic: up to 3 attempts with exponential back-off (2s → 4s → 8s).
A message that exhausts all retries is dead-lettered (nacked without requeue).

Event types handled:
  order_confirmed, order_shipped, order_delivered,
  pickup_ready, return_approved, price_alert, restock_alert
"""

import json
import logging
import os
import sys
import time
import uuid

import httpx
import pika
import psycopg
from psycopg_pool import ConnectionPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [notification_worker] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_BASE_S = 2


# ---------------------------------------------------------------------------
# DB pool
# ---------------------------------------------------------------------------

_pool: ConnectionPool | None = None


def _init_pool() -> None:
    global _pool  # noqa: PLW0603
    _pool = ConnectionPool(
        os.getenv("DATABASE_URL", ""),
        min_size=1,
        max_size=3,
        kwargs={"autocommit": True},
    )


def _get_conn():
    assert _pool is not None
    return _pool.connection()


# ---------------------------------------------------------------------------
# User data loader
# ---------------------------------------------------------------------------


def _load_user(user_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT name, email, phone FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {"name": row[0], "email": row[1], "phone": row[2]}


def _load_prefs(user_id: str) -> dict:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT sms_order_updates, email_order_updates
            FROM notification_preferences WHERE user_id = %s
            """,
            (user_id,),
        ).fetchone()
    if row:
        return {"sms": bool(row[0]), "email": bool(row[1])}
    return {"sms": True, "email": True}  # defaults when no preferences row exists


# ---------------------------------------------------------------------------
# SMS sender
# ---------------------------------------------------------------------------


def _send_sms(to: str, message: str) -> bool:
    provider = os.getenv("SMS_PROVIDER", "none")

    if provider == "twilio":
        sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        token = os.getenv("TWILIO_AUTH_TOKEN", "")
        from_num = os.getenv("TWILIO_FROM_NUMBER", "")
        if not all([sid, token, from_num]):
            return False
        try:
            resp = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                data={"From": from_num, "To": to, "Body": message},
                auth=(sid, token),
                timeout=10,
            )
            return resp.status_code == 201
        except httpx.HTTPError:
            return False

    if provider == "generic_http":
        url = os.getenv("GENERIC_SMS_URL", "")
        api_key = os.getenv("GENERIC_SMS_API_KEY", "")
        sender_id = os.getenv("GENERIC_SMS_SENDER_ID", "")
        if not all([url, api_key]):
            return False
        try:
            resp = httpx.get(
                url,
                params={
                    "api_key": api_key,
                    "sender_id": sender_id,
                    "number": to,
                    "message": message,
                },
                timeout=10,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    return False  # provider == "none"


# ---------------------------------------------------------------------------
# Email sender (SendGrid)
# ---------------------------------------------------------------------------


def _send_email(to: str, subject: str, html: str) -> bool:
    api_key = os.getenv("SENDGRID_API_KEY", "")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "")
    from_name = os.getenv("SENDGRID_FROM_NAME", "Amazra")
    if not all([api_key, from_email]):
        return False
    try:
        resp = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": to}]}],
                "from": {"email": from_email, "name": from_name},
                "subject": subject,
                "content": [{"type": "text/html", "value": html}],
            },
            timeout=10,
        )
        return resp.status_code == 202
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------
# Notification log
# ---------------------------------------------------------------------------


def _log_notification(
    user_id: str | None,
    channel: str,
    event_type: str,
    recipient: str,
    status: str,
    attempts: int,
    error: str | None = None,
) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO notification_log
              (id, user_id, channel, event_type, recipient, status,
               attempts, last_error, sent_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    CASE WHEN %s = 'sent' THEN NOW() ELSE NULL END)
            """,
            (
                str(uuid.uuid4()), user_id, channel, event_type, recipient,
                status, attempts, error, status,
            ),
        )


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------


def _sms_text(event_type: str, data: dict, user_name: str) -> str | None:
    templates: dict[str, str] = {
        "order_confirmed": (
            f"Hi {user_name}, your Amazra order #{data.get('reference')} is confirmed! "
            f"Total: BDT {data.get('total_amount', '')}. Thank you!"
        ),
        "order_shipped": (
            f"Your order #{data.get('reference')} is on its way via "
            f"{data.get('courier', 'courier')}! "
            f"Tracking: {data.get('tracking_number', 'N/A')}. - Amazra"
        ),
        "order_delivered": (
            f"Your order #{data.get('reference')} has been delivered. "
            "Thank you for shopping at Amazra!"
        ),
        "pickup_ready": (
            f"Your order #{data.get('reference')} is ready for pickup at "
            f"{data.get('branch_name', 'our branch')}. Please bring this SMS. - Amazra"
        ),
        "return_approved": (
            f"Your return for order #{data.get('reference')} is approved. "
            "Refund in 3-5 business days. - Amazra"
        ),
        "price_alert": (
            f"Price drop! {data.get('product_name', 'Your product')} is now "
            f"BDT {data.get('new_price')} on Amazra. Shop now!"
        ),
        "restock_alert": (
            f"Back in stock! {data.get('product_name', 'Your product')} is "
            "available again on Amazra. Shop now!"
        ),
    }
    return templates.get(event_type)


def _email_subject_html(
    event_type: str, data: dict, user_name: str
) -> tuple[str, str] | None:
    ref = data.get("reference", "")
    subjects: dict[str, str] = {
        "order_confirmed": f"Order Confirmed — #{ref}",
        "order_shipped": f"Your Order #{ref} Has Shipped",
        "order_delivered": f"Your Order #{ref} Has Been Delivered",
        "pickup_ready": f"Order #{ref} Ready for Pickup",
        "return_approved": f"Return Approved — Order #{ref}",
        "price_alert": f"Price Drop Alert — {data.get('product_name', 'Product')}",
        "restock_alert": f"Back In Stock — {data.get('product_name', 'Product')}",
    }
    subject = subjects.get(event_type)
    if not subject:
        return None

    sms = _sms_text(event_type, data, user_name) or ""
    html = (
        f"<html><body>"
        f"<h2>Hi {user_name},</h2>"
        f"<p>{sms}</p>"
        f"<hr><p style='font-size:12px;color:#888'>Amazra &mdash; "
        f"Bangladesh's Tech Marketplace</p>"
        f"</body></html>"
    )
    return subject, html


# ---------------------------------------------------------------------------
# Core dispatch
# ---------------------------------------------------------------------------


def _dispatch(event: dict) -> None:
    event_type = event.get("type", "")
    user_id = event.get("user_id")
    if not user_id:
        log.warning("Event missing user_id: %s", event_type)
        return

    user = _load_user(user_id)
    if not user:
        log.warning("User %s not found for event %s", user_id, event_type)
        return

    prefs = _load_prefs(user_id)

    # SMS
    if prefs["sms"] and user.get("phone"):
        sms_body = _sms_text(event_type, event, user["name"] or "")
        if sms_body:
            ok = _send_sms(user["phone"], sms_body)
            _log_notification(
                user_id, "sms", event_type, user["phone"],
                "sent" if ok else "failed", 1,
            )

    # Email
    if prefs["email"] and user.get("email"):
        result = _email_subject_html(event_type, event, user["name"] or "")
        if result:
            subject, html = result
            ok = _send_email(user["email"], subject, html)
            _log_notification(
                user_id, "email", event_type, user["email"],
                "sent" if ok else "failed", 1,
            )


# ---------------------------------------------------------------------------
# RabbitMQ consumer
# ---------------------------------------------------------------------------


def _on_message(ch, method, _props, body: bytes) -> None:
    attempt = 0
    last_err: Exception | None = None

    while attempt < _MAX_ATTEMPTS:
        attempt += 1
        try:
            event = json.loads(body.decode())
            _dispatch(event)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning(
                "Notification attempt %d/%d failed: %s",
                attempt, _MAX_ATTEMPTS, exc,
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))

    # Exhausted retries — dead-letter without requeue
    log.error("Dead-lettering message after %d attempts: %s", _MAX_ATTEMPTS, last_err)
    try:
        user_id = json.loads(body.decode()).get("user_id")
        event_type = json.loads(body.decode()).get("type", "unknown")
        _log_notification(
            user_id, "system", event_type, "unknown",
            "dead_lettered", _MAX_ATTEMPTS, str(last_err),
        )
    except Exception:  # noqa: BLE001
        pass
    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def main() -> None:
    _init_pool()
    rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    queue_name = os.getenv("NOTIFICATION_EVENTS_QUEUE_NAME", "notification_events")

    params = pika.URLParameters(rabbitmq_url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=_on_message)

    log.info("Notification worker listening on queue '%s'", queue_name)
    try:
        channel.start_consuming()
    finally:
        if _pool:
            _pool.close()
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
