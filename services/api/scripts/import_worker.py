import csv
import io
import json
import os
from decimal import Decimal

import pika
import psycopg
from jsonschema import validate as jsonschema_validate
from jsonschema.exceptions import ValidationError

from app.config import get_settings
from app.db import close_pool, get_connection, init_pool
from app.search_client import init_search
from app.search_index import upsert_product_document
from app.storage import download_bytes
from app.utils import slugify


def _validate_specs(conn, category_id, specs):
    if not category_id or specs is None:
        return
    row = conn.execute(
        "SELECT spec_schema FROM categories WHERE id = %s",
        (category_id,),
    ).fetchone()
    if row and row[0]:
        jsonschema_validate(instance=specs, schema=row[0])


def process_job(job_id: str) -> None:
    with get_connection() as conn:
        job = conn.execute(
            "SELECT file_key FROM product_import_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        if not job:
            return

        conn.execute(
            (
                "UPDATE product_import_jobs SET status = 'processing' "
                "WHERE id = %s"
            ),
            (job_id,),
        )

    content = download_bytes(job[0])
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    total_rows = 0
    processed = 0
    success = 0
    errors = 0

    with get_connection() as conn:
        for idx, row in enumerate(reader, start=1):
            total_rows += 1
            try:
                name = row["name"].strip()
                slug = row["slug"].strip() or slugify(name)
                brand_id = row["brand_id"].strip() or None
                category_id = row["category_id"].strip() or None
                price = Decimal(row["price"])
                original_price = (
                    Decimal(row["original_price"])
                    if row.get("original_price")
                    else None
                )
                status = row.get("status") or "active"
                is_featured = row.get("is_featured", "false").lower() == "true"
                specs = (
                    json.loads(row["specs_json"])
                    if row.get("specs_json")
                    else None
                )

                _validate_specs(conn, category_id, specs)

                existing = conn.execute(
                    "SELECT id, price FROM products WHERE slug = %s",
                    (slug,),
                ).fetchone()

                if existing:
                    conn.execute(
                        """
                        UPDATE products
                        SET
                          name = %s,
                          brand_id = %s,
                          category_id = %s,
                          price = %s,
                          original_price = %s,
                          specs = %s,
                          status = %s,
                          is_featured = %s,
                          updated_at = NOW()
                        WHERE id = %s
                        """,
                        (
                            name,
                            brand_id,
                            category_id,
                            price,
                            original_price,
                            json.dumps(specs) if specs is not None else None,
                            status,
                            is_featured,
                            existing[0],
                        ),
                    )
                    if existing[1] != price:
                        conn.execute(
                            """
                            INSERT INTO product_price_history (
                              product_id,
                              old_price,
                              new_price
                            )
                            VALUES (%s, %s, %s)
                            """,
                            (existing[0], existing[1], price),
                        )
                    upsert_product_document(str(existing[0]))
                else:
                    created = conn.execute(
                        """
                        INSERT INTO products (
                          name,
                          slug,
                          brand_id,
                          category_id,
                          price,
                          original_price,
                          specs,
                          status,
                          is_featured
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            name,
                            slug,
                            brand_id,
                            category_id,
                            price,
                            original_price,
                            json.dumps(specs) if specs is not None else None,
                            status,
                            is_featured,
                        ),
                    ).fetchone()
                    upsert_product_document(str(created[0]))

                success += 1
            except (
                KeyError,
                ValueError,
                ValidationError,
                json.JSONDecodeError,
                psycopg.Error,
            ) as exc:
                errors += 1
                conn.execute(
                    """
                    INSERT INTO product_import_job_errors (
                      job_id,
                      row_number,
                      error_message
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (job_id, idx, str(exc)),
                )

            processed += 1
            if processed % 20 == 0:
                conn.execute(
                    """
                    UPDATE product_import_jobs
                    SET
                      processed_rows = %s,
                      total_rows = %s,
                      success_count = %s,
                      error_count = %s,
                      updated_at = NOW()
                    WHERE id = %s
                    """,
                    (processed, total_rows, success, errors, job_id),
                )

        conn.execute(
            """
            UPDATE product_import_jobs
            SET
              processed_rows = %s,
              total_rows = %s,
              success_count = %s,
              error_count = %s,
              status = %s,
              updated_at = NOW()
            WHERE id = %s
            """,
            (
                processed,
                total_rows,
                success,
                errors,
                "completed" if errors == 0 else "completed_with_errors",
                job_id,
            ),
        )


def main() -> None:
    init_pool()
    init_search()
    settings = get_settings()
    parameters = pika.URLParameters(
        os.getenv(
            "RABBITMQ_URL",
            "amqp://guest:guest@rabbitmq:5672/",
        )
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=settings.import_queue_name, durable=True)

    def callback(ch, method, properties, body):  # noqa: ANN001
        job_id = body.decode()
        process_job(job_id)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(
        queue=settings.import_queue_name,
        on_message_callback=callback,
    )
    print("Import worker ready.")
    try:
        channel.start_consuming()
    finally:
        close_pool()
        connection.close()


if __name__ == "__main__":
    main()
