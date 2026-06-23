"""Phase 5.4 — Price & Restock Alert Worker.

Polls the database every 5 minutes for:
  - price_alerts:   products whose current price <= target price
  - restock_alerts: products that came back into stock since last check

Triggered alerts are published to the notification_events queue and then
deactivated (triggered_at is set) so they fire exactly once.

Run this script as a long-lived process alongside the API.
"""

import json
import logging
import os
import time

import pika
import psycopg
from psycopg_pool import ConnectionPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [alert_worker] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

_POLL_INTERVAL_S = int(os.getenv("ALERT_POLL_INTERVAL_SECONDS", "300"))  # 5 min

_pool: ConnectionPool | None = None


def _init_pool() -> None:
    global _pool  # noqa: PLW0603
    _pool = ConnectionPool(
        os.getenv("DATABASE_URL", ""),
        min_size=1,
        max_size=2,
        kwargs={"autocommit": True},
    )


def _get_conn():
    assert _pool is not None
    return _pool.connection()


# ---------------------------------------------------------------------------
# RabbitMQ
# ---------------------------------------------------------------------------


def _get_channel(rabbitmq_url: str, queue_name: str):
    params = pika.URLParameters(rabbitmq_url)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.queue_declare(queue=queue_name, durable=True)
    return conn, ch


def _publish(ch, queue_name: str, message: dict) -> None:
    ch.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=json.dumps(message).encode(),
        properties=pika.BasicProperties(delivery_mode=2),  # persistent
    )


# ---------------------------------------------------------------------------
# Price alert checks
# ---------------------------------------------------------------------------


def _check_price_alerts(ch, queue_name: str) -> int:
    """Find price alerts where current price has dropped to/below target. Returns count."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT pa.id, pa.user_id, pa.product_id, pa.target_price,
                   p.name, p.price
            FROM price_alerts pa
            JOIN products p ON p.id = pa.product_id
            WHERE pa.is_active
              AND p.price <= pa.target_price
            """,
        ).fetchall()

        triggered = 0
        for row in rows:
            alert_id, user_id, product_id, target, prod_name, current_price = row
            _publish(ch, queue_name, {
                "type": "price_alert",
                "user_id": str(user_id),
                "product_id": str(product_id),
                "product_name": prod_name,
                "target_price": str(target),
                "new_price": str(current_price),
            })
            conn.execute(
                """
                UPDATE price_alerts
                SET is_active = FALSE, triggered_at = NOW()
                WHERE id = %s
                """,
                (alert_id,),
            )
            triggered += 1

    return triggered


# ---------------------------------------------------------------------------
# Restock alert checks
# ---------------------------------------------------------------------------


def _check_restock_alerts(ch, queue_name: str) -> int:
    """Find restock alerts where total available inventory > 0. Returns count."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ra.id, ra.user_id, ra.product_id, p.name,
                   COALESCE(SUM(i.quantity_available), 0) AS total_qty
            FROM restock_alerts ra
            JOIN products p ON p.id = ra.product_id
            LEFT JOIN inventory i ON i.product_id = ra.product_id
            WHERE ra.is_active
            GROUP BY ra.id, ra.user_id, ra.product_id, p.name
            HAVING COALESCE(SUM(i.quantity_available), 0) > 0
            """,
        ).fetchall()

        triggered = 0
        for row in rows:
            alert_id, user_id, product_id, prod_name, qty = row
            _publish(ch, queue_name, {
                "type": "restock_alert",
                "user_id": str(user_id),
                "product_id": str(product_id),
                "product_name": prod_name,
                "quantity_available": int(qty),
            })
            conn.execute(
                """
                UPDATE restock_alerts
                SET is_active = FALSE, triggered_at = NOW()
                WHERE id = %s
                """,
                (alert_id,),
            )
            triggered += 1

    return triggered


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    _init_pool()
    rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    queue_name = os.getenv("NOTIFICATION_EVENTS_QUEUE_NAME", "notification_events")

    log.info("Alert worker started. Poll interval: %ds", _POLL_INTERVAL_S)

    while True:
        rmq_conn, ch = None, None
        try:
            rmq_conn, ch = _get_channel(rabbitmq_url, queue_name)
            price_count = _check_price_alerts(ch, queue_name)
            restock_count = _check_restock_alerts(ch, queue_name)
            if price_count or restock_count:
                log.info(
                    "Fired %d price alert(s) and %d restock alert(s)",
                    price_count, restock_count,
                )
        except Exception as exc:  # noqa: BLE001
            log.error("Alert check failed: %s", exc)
        finally:
            if ch:
                try:
                    ch.connection.close()
                except Exception:  # noqa: BLE001
                    pass

        time.sleep(_POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
