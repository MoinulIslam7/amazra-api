import os
import re
from typing import Iterable

import bcrypt
import psycopg
from psycopg.types.json import Json


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "item"


def seed_roles(conn: psycopg.Connection) -> None:
    roles = [
        ("admin", "Full access"),
        ("staff", "Branch staff access"),
        ("customer", "Customer access"),
    ]
    for name, description in roles:
        conn.execute(
            """
            INSERT INTO roles (name, description)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING;
            """,
            (name, description),
        )


def seed_branches(conn: psycopg.Connection) -> None:
    branches = [
        ("Dhaka HQ", "DHK-HQ", "12/B, Tech Street", "Dhaka"),
        ("Chattogram", "CTG-01", "45/2, Commerce Road", "Chattogram"),
    ]
    for name, code, address, city in branches:
        conn.execute(
            """
            INSERT INTO branches (name, code, address, city)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (code) DO NOTHING;
            """,
            (name, code, address, city),
        )


def seed_admin(conn: psycopg.Connection) -> None:
    admin_email = os.getenv("FIRST_ADMIN_EMAIL", "admin@amazra.com")
    admin_password = os.getenv("FIRST_ADMIN_PASSWORD", "admin12345")
    admin_phone = "+8801000000000"

    role_id = conn.execute(
        "SELECT id FROM roles WHERE name = %s LIMIT 1", ("admin",)
    ).fetchone()
    branch_id = conn.execute(
        "SELECT id FROM branches ORDER BY created_at LIMIT 1"
    ).fetchone()

    if not role_id:
        raise RuntimeError("Admin role not found")

    password_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
    conn.execute(
        """
        INSERT INTO users (name, email, phone, password_hash, role_id, branch_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (email) DO NOTHING;
        """,
        (
            "Admin User",
            admin_email,
            admin_phone,
            password_hash,
            role_id[0],
            branch_id[0] if branch_id else None,
        ),
    )


def seed_products(conn: psycopg.Connection) -> None:
    products: Iterable[tuple[str, float, dict]] = [
        ("Amazra Laptop Pro 14", 98000, {"category": "laptop", "brand": "Amazra"}),
        ("Amazra Laptop Air 13", 82000, {"category": "laptop", "brand": "Amazra"}),
        ("Amazra Gaming GPU X1", 42000, {"category": "gpu", "brand": "Amazra"}),
        ("Amazra Ryzen Motherboard", 18000, {"category": "motherboard", "brand": "Amazra"}),
        ("Amazra DDR5 32GB Kit", 14000, {"category": "ram", "brand": "Amazra"}),
        ("Amazra NVMe 1TB", 9500, {"category": "storage", "brand": "Amazra"}),
        ("Amazra 650W PSU", 7200, {"category": "psu", "brand": "Amazra"}),
        ("Amazra ATX Case Mid", 6000, {"category": "case", "brand": "Amazra"}),
        ("Amazra IPS Monitor 24", 17500, {"category": "monitor", "brand": "Amazra"}),
        ("Amazra Wireless Mouse", 1200, {"category": "accessory", "brand": "Amazra"}),
    ]

    for name, price, metadata in products:
        conn.execute(
            """
            INSERT INTO products (name, slug, price, metadata)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (slug) DO NOTHING;
            """,
            (name, slugify(name), price, Json(metadata)),
        )


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            seed_roles(conn)
            seed_branches(conn)
            seed_admin(conn)
            seed_products(conn)

    print("Seed data inserted.")


if __name__ == "__main__":
    main()
