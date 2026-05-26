import os

import pika


def publish_message(queue_name: str, message: str) -> None:
    parameters = pika.URLParameters(
        os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=message.encode(),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()
