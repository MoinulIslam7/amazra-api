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
        ("Dhaka HQ", "DHK-HQ", "12/B, Tech Street", "Dhaka", "+8801999000000"),
        (
            "Chattogram",
            "CTG-01",
            "45/2, Commerce Road",
            "Chattogram",
            "+8801999000001",
        ),
    ]
    for name, code, address, city, phone in branches:
        conn.execute(
            """
            INSERT INTO branches (name, code, address, city, phone, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (code) DO NOTHING;
            """,
            (name, code, address, city, phone),
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

    password_hash = bcrypt.hashpw(
        admin_password.encode(),
        bcrypt.gensalt(),
    ).decode()
    conn.execute(
        """
        INSERT INTO users (
          name,
          email,
          phone,
          password_hash,
          role_id,
          branch_id
        )
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


def seed_categories(conn: psycopg.Connection) -> dict[str, str]:
    top_level = [
        "Laptops",
        "Desktops",
        "Components",
        "Monitors",
        "Accessories",
        "Storage",
        "Networking",
        "Cameras",
        "Mobile Phones",
        "Tablets",
        "Smart Watch",
        "Printers",
        "Office Equipment",
        "Security",
        "Software",
        "Gaming",
        "TV & Audio",
        "Power & UPS",
        "Gadgets",
        "Servers & Workstations",
        "Projectors",
    ]
    for name in top_level:
        conn.execute(
            """
            INSERT INTO categories (name, slug)
            VALUES (%s, %s)
            ON CONFLICT (slug) DO NOTHING;
            """,
            (name, slugify(name)),
        )

    rows = conn.execute(
        "SELECT id, slug FROM categories WHERE parent_id IS NULL"
    ).fetchall()
    category_ids = {row[1]: row[0] for row in rows}

    subcategories = [
        ("laptops", "Gaming Laptops"),
        ("laptops", "Ultrabooks"),
        ("laptops", "Business Laptops"),
        ("laptops", "2-in-1 Laptops"),
        ("laptops", "Budget Laptops"),
        ("desktops", "Gaming PC"),
        ("desktops", "All-in-One PC"),
        ("desktops", "Mini PC"),
        ("desktops", "Workstation PC"),
        ("components", "Processor"),
        ("components", "Motherboard"),
        ("components", "RAM"),
        ("components", "Graphics Card"),
        ("components", "Power Supply"),
        ("components", "Casing"),
        ("components", "CPU Cooler"),
        ("components", "SSD"),
        ("components", "HDD"),
        ("components", "Optical Drive"),
        ("monitors", "Gaming Monitor"),
        ("monitors", "4K Monitor"),
        ("monitors", "Curved Monitor"),
        ("monitors", "Professional Monitor"),
        ("accessories", "Mouse"),
        ("accessories", "Keyboard"),
        ("accessories", "Headset"),
        ("accessories", "Speakers"),
        ("accessories", "Webcam"),
        ("accessories", "Mouse Pad"),
        ("accessories", "USB Hub"),
        ("accessories", "Laptop Bag"),
        ("storage", "External HDD"),
        ("storage", "External SSD"),
        ("storage", "Pen Drive"),
        ("storage", "Memory Card"),
        ("networking", "Router"),
        ("networking", "Switch"),
        ("networking", "Access Point"),
        ("networking", "LAN Card"),
        ("networking", "Network Cable"),
        ("cameras", "DSLR"),
        ("cameras", "Mirrorless"),
        ("cameras", "Action Cam"),
        ("cameras", "CCTV Camera"),
        ("mobile-phones", "Android Phones"),
        ("mobile-phones", "iPhones"),
        ("mobile-phones", "Feature Phones"),
        ("tablets", "Android Tablets"),
        ("tablets", "iPad"),
        ("smart-watch", "Fitness Bands"),
        ("smart-watch", "Smart Watches"),
        ("printers", "Inkjet Printer"),
        ("printers", "Laser Printer"),
        ("printers", "Printer Toner"),
        ("printers", "Printer Accessories"),
        ("office-equipment", "Scanner"),
        ("security", "DVR"),
        ("security", "NVR"),
        ("security", "Biometric"),
        ("security", "IP Camera"),
        ("software", "Antivirus"),
        ("software", "OS Licenses"),
        ("gaming", "Gaming Chair"),
        ("gaming", "Gamepad"),
        ("gaming", "VR Headset"),
        ("tv-audio", "Smart TV"),
        ("tv-audio", "Soundbar"),
        ("tv-audio", "Home Theater"),
        ("power-ups", "UPS"),
        ("power-ups", "Power Strip"),
        ("power-ups", "Battery"),
        ("gadgets", "Smart Home"),
        ("gadgets", "Car Accessories"),
        ("servers-workstations", "Server"),
        ("servers-workstations", "Rack"),
        ("servers-workstations", "NAS"),
        ("projectors", "Portable Projector"),
        ("projectors", "Business Projector"),
    ]

    for parent_slug, name in subcategories:
        parent_id = category_ids.get(parent_slug)
        if not parent_id:
            continue
        conn.execute(
            """
            INSERT INTO categories (name, slug, parent_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (slug) DO NOTHING;
            """,
            (name, slugify(name), parent_id),
        )

    return category_ids


def seed_brands(conn: psycopg.Connection) -> dict[str, str]:
    brands = [
        "Amazra",
        "Asus",
        "HP",
        "Lenovo",
        "MSI",
        "Dell",
        "Acer",
        "Apple",
        "Samsung",
        "Xiaomi",
        "Huawei",
        "Gigabyte",
        "Intel",
        "AMD",
        "NVIDIA",
        "Corsair",
        "Logitech",
        "Razer",
        "TP-Link",
        "Netgear",
        "D-Link",
        "Canon",
        "Nikon",
        "Sony",
        "Seagate",
        "Western Digital",
        "Kingston",
        "Crucial",
        "SanDisk",
        "Transcend",
        "ViewSonic",
        "BenQ",
        "LG",
        "Toshiba",
        "Panasonic",
        "Epson",
        "Brother",
        "Hikvision",
        "Dahua",
        "ZKTeco",
        "Asrock",
        "Biostar",
        "Cooler Master",
        "Antec",
        "Thermaltake",
        "Deepcool",
        "Adata",
        "Intelbras",
        "JBL",
        "Bose",
        "Fantech",
        "Redragon",
    ]
    for name in brands:
        conn.execute(
            """
            INSERT INTO brands (name, slug)
            VALUES (%s, %s)
            ON CONFLICT (slug) DO NOTHING;
            """,
            (name, slugify(name)),
        )

    rows = conn.execute("SELECT id, slug FROM brands").fetchall()
    return {row[1]: row[0] for row in rows}


def seed_products(conn: psycopg.Connection) -> None:
    brand_rows = conn.execute("SELECT id, slug FROM brands").fetchall()
    brand_ids = {row[1]: row[0] for row in brand_rows}
    category_rows = conn.execute("SELECT id, slug FROM categories").fetchall()
    category_ids = {row[1]: row[0] for row in category_rows}
    products: Iterable[tuple[str, float, str, str, dict]] = [
        (
            "Amazra Laptop Pro 14",
            98000,
            "amazra",
            "laptops",
            {"cpu": "Intel Core i7", "ram": "16GB", "storage": "512GB SSD"},
        ),
        (
            "Amazra Laptop Air 13",
            82000,
            "amazra",
            "laptops",
            {"cpu": "Intel Core i5", "ram": "8GB", "storage": "256GB SSD"},
        ),
        (
            "Amazra Gaming GPU X1",
            42000,
            "amazra",
            "components",
            {"memory": "8GB GDDR6", "chipset": "NVIDIA"},
        ),
        (
            "Amazra Ryzen Motherboard",
            18000,
            "amazra",
            "components",
            {"socket": "AM4", "form_factor": "ATX"},
        ),
        (
            "Amazra DDR5 32GB Kit",
            14000,
            "amazra",
            "components",
            {"speed": "5200MHz", "type": "DDR5"},
        ),
        (
            "Amazra NVMe 1TB",
            9500,
            "amazra",
            "storage",
            {"interface": "NVMe", "capacity": "1TB"},
        ),
        (
            "Amazra 650W PSU",
            7200,
            "amazra",
            "components",
            {"efficiency": "80+ Bronze"},
        ),
        (
            "Amazra ATX Case Mid",
            6000,
            "amazra",
            "components",
            {"color": "Black", "fan_support": "3x120mm"},
        ),
        (
            "Amazra IPS Monitor 24",
            17500,
            "amazra",
            "monitors",
            {"panel": "IPS", "resolution": "1920x1080"},
        ),
        (
            "Amazra Wireless Mouse",
            1200,
            "amazra",
            "accessories",
            {"dpi": "1600", "battery": "AA"},
        ),
    ]

    for name, price, brand_slug, category_slug, specs in products:
        conn.execute(
            """
            INSERT INTO products (
              name,
              slug,
              price,
              brand_id,
              category_id,
              specs
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO NOTHING;
            """,
            (
                name,
                slugify(name),
                price,
                brand_ids.get(brand_slug),
                category_ids.get(category_slug),
                Json(specs),
            ),
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
            seed_categories(conn)
            seed_brands(conn)
            seed_products(conn)

    print("Seed data inserted.")


if __name__ == "__main__":
    main()
