import os

import pika

from app.config import get_settings
from app.db import close_pool, init_pool
from app.search_client import init_search
from app.search_index import reindex_all_products


def main() -> None:
    """Consume reindex jobs and rebuild the Elasticsearch index."""
    init_pool()
    init_search()
    settings = get_settings()
    parameters = pika.URLParameters(
        os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(
        queue=settings.search_reindex_queue_name, durable=True
    )

    def callback(ch, method, properties, body):  # noqa: ANN001
        job_id = body.decode()
        new_index = reindex_all_products()
        print(f"Search reindex {job_id} complete: {new_index}")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(
        queue=settings.search_reindex_queue_name,
        on_message_callback=callback,
    )
    print("Search index worker ready.")
    try:
        channel.start_consuming()
    finally:
        close_pool()
        connection.close()


if __name__ == "__main__":
    main()
