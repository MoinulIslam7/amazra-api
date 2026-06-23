import time
from collections import defaultdict

from app.db import close_pool, get_connection, init_pool
from app.search_client import init_search
from app.search_index import update_product_stock


def release_expired_reservations() -> int:
    """Release reservations whose TTL has expired."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, product_id, branch_id, quantity
            FROM inventory_reservations
            WHERE expires_at <= NOW()
            """
        ).fetchall()
        if not rows:
            return 0

        grouped = defaultdict(int)
        for row in rows:
            grouped[(str(row[1]), str(row[2]))] += row[3]

        with conn.transaction():
            for (product_id, branch_id), quantity in grouped.items():
                conn.execute(
                    """
                    UPDATE inventory
                    SET reserved_qty = GREATEST(reserved_qty - %s, 0),
                        updated_at = NOW()
                    WHERE product_id = %s AND branch_id = %s
                    """,
                    (quantity, product_id, branch_id),
                )
            ids = [row[0] for row in rows]
            conn.execute(
                "DELETE FROM inventory_reservations WHERE id = ANY(%s)",
                (ids,),
            )

    for product_id, _branch_id in grouped.keys():
        update_product_stock(product_id)
    return len(rows)


def main() -> None:
    """Run the reservation reaper on a 5-minute interval."""
    init_pool()
    init_search()
    try:
        while True:
            released = release_expired_reservations()
            if released:
                print(f"Released {released} expired reservations.")
            time.sleep(300)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
