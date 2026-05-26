import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Json

from .db import get_connection
from .deps import require_admin
from .redis_client import get_redis
from .utils import slugify

router = APIRouter(prefix="/categories", tags=["categories"])
admin_router = APIRouter(prefix="/admin/categories", tags=["categories"])


class CategoryRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    slug: Optional[str] = None
    parent_id: Optional[str] = None
    sort_order: int = 0
    spec_schema: Optional[dict] = None
    is_active: bool = True


def _build_tree(rows):
    nodes = {}
    tree = []
    for row in rows:
        node = {
            "id": str(row[0]),
            "name": row[1],
            "slug": row[2],
            "parent_id": str(row[3]) if row[3] else None,
            "sort_order": row[4],
            "is_active": row[5],
            "children": [],
        }
        nodes[node["id"]] = node

    for node in nodes.values():
        if node["parent_id"] and node["parent_id"] in nodes:
            nodes[node["parent_id"]]["children"].append(node)
        else:
            tree.append(node)

    return tree


def _normalize_id(value: Optional[str]) -> Optional[str]:
    return str(value) if value else None


def _validate_parent(
    conn,
    parent_id: Optional[str],
    category_id: Optional[str] = None,
) -> None:
    if not parent_id:
        return

    row = conn.execute(
        """
        SELECT id, parent_id
        FROM categories
        WHERE id = %s AND deleted_at IS NULL
        """,
        (parent_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid parent_id")

    if category_id and _normalize_id(row[0]) == category_id:
        raise HTTPException(
            status_code=400,
            detail="Category cannot be its own parent",
        )

    if category_id:
        current_parent = row[1]
        visited = {_normalize_id(row[0])}
        while current_parent:
            current_id = _normalize_id(current_parent)
            if current_id == category_id:
                raise HTTPException(
                    status_code=400,
                    detail="Category parent creates a cycle",
                )
            if current_id in visited:
                break
            visited.add(current_id)
            next_row = conn.execute(
                """
                SELECT parent_id
                FROM categories
                WHERE id = %s AND deleted_at IS NULL
                """,
                (current_parent,),
            ).fetchone()
            if not next_row:
                break
            current_parent = next_row[0]


@router.get("")
def list_categories():
    redis_client = get_redis()
    cached = redis_client.get("categories:tree")
    if cached:
        return json.loads(cached)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, slug, parent_id, sort_order, is_active
            FROM categories
            WHERE deleted_at IS NULL
            ORDER BY sort_order ASC, name ASC
            """
        ).fetchall()

    tree = _build_tree(rows)
    redis_client.setex("categories:tree", 3600, json.dumps(tree))
    return tree


@admin_router.get("")
def admin_list_categories(user=Depends(require_admin)):
    return list_categories()


@router.get("/{slug}")
def get_category(slug: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              id,
              name,
              slug,
              parent_id,
              sort_order,
              is_active,
              spec_schema
            FROM categories
            WHERE slug = %s AND deleted_at IS NULL
            """,
            (slug,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Category not found")

        children = conn.execute(
            """
            SELECT id, name, slug, parent_id, sort_order, is_active
            FROM categories
            WHERE parent_id = %s AND deleted_at IS NULL
            ORDER BY sort_order ASC, name ASC
            """,
            (row[0],),
        ).fetchall()

    return {
        "id": str(row[0]),
        "name": row[1],
        "slug": row[2],
        "parent_id": str(row[3]) if row[3] else None,
        "sort_order": row[4],
        "is_active": row[5],
        "spec_schema": row[6],
        "children": [
            {
                "id": str(child[0]),
                "name": child[1],
                "slug": child[2],
                "parent_id": str(child[3]) if child[3] else None,
                "sort_order": child[4],
                "is_active": child[5],
            }
            for child in children
        ],
    }


@admin_router.get("/{slug}")
def admin_get_category(slug: str, user=Depends(require_admin)):
    return get_category(slug)


@router.post("")
@admin_router.post("")
def create_category(payload: CategoryRequest, user=Depends(require_admin)):
    category_slug = payload.slug or slugify(payload.name)
    with get_connection() as conn:
        try:
            _validate_parent(conn, payload.parent_id)
            spec_schema = (
                Json(payload.spec_schema)
                if payload.spec_schema is not None
                else None
            )
            row = conn.execute(
                """
                INSERT INTO categories (
                  name,
                  slug,
                  parent_id,
                  sort_order,
                  spec_schema,
                  is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    payload.name,
                    category_slug,
                    payload.parent_id,
                    payload.sort_order,
                    spec_schema,
                    payload.is_active,
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="Category slug exists",
            ) from exc

    get_redis().delete("categories:tree")
    return {"id": str(row[0])}


@router.put("/{category_id}")
@admin_router.put("/{category_id}")
def update_category(
    category_id: str,
    payload: CategoryRequest,
    user=Depends(require_admin),
):
    category_slug = payload.slug or slugify(payload.name)
    with get_connection() as conn:
        try:
            _validate_parent(conn, payload.parent_id, category_id=category_id)
            spec_schema = (
                Json(payload.spec_schema)
                if payload.spec_schema is not None
                else None
            )
            updated = conn.execute(
                """
                UPDATE categories
                SET name = %s,
                    slug = %s,
                    parent_id = %s,
                    sort_order = %s,
                    spec_schema = %s,
                    is_active = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    payload.name,
                    category_slug,
                    payload.parent_id,
                    payload.sort_order,
                    spec_schema,
                    payload.is_active,
                    category_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="Category slug exists",
            ) from exc

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Category not found")

    get_redis().delete("categories:tree")
    return {"status": "updated"}


@router.delete("/{category_id}")
@admin_router.delete("/{category_id}")
def delete_category(category_id: str, user=Depends(require_admin)):
    with get_connection() as conn:
        count_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM products
            WHERE category_id = %s AND status = 'active'
            """,
            (category_id,),
        ).fetchone()

        if count_row and count_row[0] > 0:
            raise HTTPException(
                status_code=409, detail="Category has active products"
            )

        updated = conn.execute(
            """
            UPDATE categories
            SET is_active = FALSE, deleted_at = NOW()
            WHERE id = %s
            """,
            (category_id,),
        )

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Category not found")

    get_redis().delete("categories:tree")
    return {"status": "deleted"}
