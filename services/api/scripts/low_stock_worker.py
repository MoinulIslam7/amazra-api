import json
import os

import pika

from app.config import get_settings
from app.db import close_pool, get_connection, init_pool


def _notify_branch_managers(payload: dict) -> None:
    """Send low-stock notifications to branch staff (stdout in dev)."""
    with get_connection() as conn:
        branch = conn.execute(
            "SELECT name, phone FROM branches WHERE id = %s",
            (payload["branch_id"],),
        ).fetchone()
        product = conn.execute(
            "SELECT name FROM products WHERE id = %s",
            (payload["product_id"],),
        ).fetchone()
        recipients = conn.execute(
            """
            SELECT users.email
            FROM users
            LEFT JOIN roles ON users.role_id = roles.id
            WHERE users.branch_id = %s
              AND roles.name IN ('admin', 'staff')
              AND users.email IS NOT NULL
            """,
            (payload["branch_id"],),
        ).fetchall()

    branch_name = branch[0] if branch else "Unknown branch"
    product_name = product[0] if product else "Unknown product"
    emails = [row[0] for row in recipients]
    message = (
        f"Low stock alert: {product_name} at {branch_name} "
        f"(available {payload['available']}, threshold {payload['threshold']})"
    )
    # Replace this with a real email integration in production.
    print(f"Notify {emails or ['no-recipients']}: {message}")


def main() -> None:
    """Consume low-stock events and notify branch managers."""
    init_pool()
    settings = get_settings()
    parameters = pika.URLParameters(
        os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=settings.low_stock_queue_name, durable=True)

    def callback(ch, method, properties, body):  # noqa: ANN001
        payload = json.loads(body.decode())
        _notify_branch_managers(payload)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(
        queue=settings.low_stock_queue_name,
        on_message_callback=callback,
    )
    print("Low stock worker ready.")
    try:
        channel.start_consuming()
    finally:
        close_pool()
        connection.close()


if __name__ == "__main__":
    main()
