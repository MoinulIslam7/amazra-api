from fastapi import APIRouter, Depends

from .db import get_connection
from .deps import require_admin

router = APIRouter(prefix="/admin/analytics", tags=["analytics"])

EXCLUDE_CANCELLED = "status != 'cancelled'"


@router.get("/sales")
def sales_summary(
    start_date: str,
    end_date: str,
    user=Depends(require_admin),
):
    """Daily revenue/order-count series plus totals for a date range."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT date_trunc('day', created_at) AS day,
                   COUNT(*) AS order_count,
                   COALESCE(SUM(total_amount), 0) AS revenue
            FROM orders
            WHERE created_at BETWEEN %s AND %s AND {EXCLUDE_CANCELLED}
            GROUP BY day
            ORDER BY day
            """,
            (start_date, end_date),
        ).fetchall()

    daily = [
        {
            "date": row[0].date().isoformat(),
            "order_count": row[1],
            "revenue": str(row[2]),
        }
        for row in rows
    ]
    total_orders = sum(d["order_count"] for d in daily)
    total_revenue = sum(float(d["revenue"]) for d in daily)
    aov = total_revenue / total_orders if total_orders else 0

    return {
        "daily": daily,
        "total_orders": total_orders,
        "total_revenue": str(round(total_revenue, 2)),
        "average_order_value": str(round(aov, 2)),
    }


@router.get("/by-category")
def revenue_by_category(
    start_date: str,
    end_date: str,
    user=Depends(require_admin),
):
    """Revenue and units sold grouped by product category."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
              COALESCE(c.id::text, 'uncategorized'),
              COALESCE(c.name, 'Uncategorized'),
              SUM(oi.quantity),
              SUM(oi.total_price)
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE o.created_at BETWEEN %s AND %s AND o.{EXCLUDE_CANCELLED}
            GROUP BY c.id, c.name
            ORDER BY SUM(oi.total_price) DESC
            """,
            (start_date, end_date),
        ).fetchall()

    return [
        {
            "category_id": row[0],
            "category_name": row[1],
            "units_sold": row[2],
            "revenue": str(row[3]),
        }
        for row in rows
    ]


@router.get("/by-branch")
def revenue_by_branch(
    start_date: str,
    end_date: str,
    user=Depends(require_admin),
):
    """Revenue and order count grouped by fulfilment branch."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT b.id, b.name, COUNT(DISTINCT o.id), COALESCE(SUM(o.total_amount), 0)
            FROM orders o
            JOIN branches b ON b.id = o.branch_id
            WHERE o.created_at BETWEEN %s AND %s AND o.{EXCLUDE_CANCELLED}
            GROUP BY b.id, b.name
            ORDER BY SUM(o.total_amount) DESC
            """,
            (start_date, end_date),
        ).fetchall()

    return [
        {
            "branch_id": str(row[0]),
            "branch_name": row[1],
            "order_count": row[2],
            "revenue": str(row[3]),
        }
        for row in rows
    ]


@router.get("/top-products")
def top_products(
    start_date: str,
    end_date: str,
    limit: int = 20,
    user=Depends(require_admin),
):
    """Best-selling products by units sold for a date range."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.name, p.slug, SUM(oi.quantity), SUM(oi.total_price)
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            WHERE o.created_at BETWEEN %s AND %s AND o.{EXCLUDE_CANCELLED}
            GROUP BY p.id, p.name, p.slug
            ORDER BY SUM(oi.quantity) DESC
            LIMIT %s
            """,
            (start_date, end_date, limit),
        ).fetchall()

    return [
        {
            "product_id": str(row[0]),
            "product_name": row[1],
            "product_slug": row[2],
            "units_sold": row[3],
            "revenue": str(row[4]),
        }
        for row in rows
    ]


@router.get("/customers")
def customer_acquisition(
    start_date: str,
    end_date: str,
    user=Depends(require_admin),
):
    """Daily new-vs-returning customer counts based on each user's first order."""
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            WITH first_orders AS (
              SELECT user_id, MIN(created_at) AS first_order_at
              FROM orders
              WHERE {EXCLUDE_CANCELLED}
              GROUP BY user_id
            ),
            orders_in_range AS (
              SELECT o.id, o.user_id, date_trunc('day', o.created_at) AS day,
                     fo.first_order_at
              FROM orders o
              JOIN first_orders fo ON fo.user_id = o.user_id
              WHERE o.created_at BETWEEN %s AND %s AND o.{EXCLUDE_CANCELLED}
            )
            SELECT
              day,
              COUNT(DISTINCT CASE WHEN date_trunc('day', first_order_at) = day THEN user_id END),
              COUNT(DISTINCT CASE WHEN date_trunc('day', first_order_at) < day THEN user_id END)
            FROM orders_in_range
            GROUP BY day
            ORDER BY day
            """,
            (start_date, end_date),
        ).fetchall()

    return [
        {
            "date": row[0].date().isoformat(),
            "new_customers": row[1],
            "returning_customers": row[2],
        }
        for row in rows
    ]
