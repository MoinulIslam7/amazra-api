import os

import pika
import psycopg
import redis
from elasticsearch import Elasticsearch


def check_postgres() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT 1;")


def check_redis() -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required")
    client = redis.from_url(redis_url)
    client.ping()


def check_elasticsearch() -> None:
    es_url = os.getenv("ELASTICSEARCH_URL")
    if not es_url:
        raise RuntimeError("ELASTICSEARCH_URL is required")
    client = Elasticsearch(es_url)
    client.info()


def check_rabbitmq() -> None:
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        raise RuntimeError("RABBITMQ_URL is required")
    connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
    connection.close()


def main() -> None:
    check_postgres()
    check_redis()
    check_elasticsearch()
    check_rabbitmq()
    print("All connections verified.")


if __name__ == "__main__":
    main()
