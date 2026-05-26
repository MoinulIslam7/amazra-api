from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from jsonschema import validate as jsonschema_validate
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, Field
from psycopg.types.json import Json

from .db import get_connection
from .deps import require_admin
from .storage import (
    delete_objects,
    generate_image_set_id,
    resize_image,
    upload_bytes,
)
from .utils import slugify

router = APIRouter(prefix="/products", tags=["products"])
admin_router = APIRouter(prefix="/admin/products", tags=["products"])


class ProductRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=500)
    slug: Optional[str] = None
    brand_id: Optional[str] = None
    category_id: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    original_price: Optional[Decimal] = None
    specs: Optional[dict] = None
    status: str = "active"
    is_featured: bool = False
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None


class ProductStatusRequest(BaseModel):
    status: str = Field(..., min_length=3, max_length=20)


def _validate_specs(category_id: Optional[str], specs: Optional[dict]) -> None:
    if not category_id or specs is None:
        return
    with get_connection() as conn:
        row = conn.execute(
            "SELECT spec_schema FROM categories WHERE id = %s",
            (category_id,),
        ).fetchone()
    if row and row[0]:
        try:
            jsonschema_validate(instance=specs, schema=row[0])
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_relations(
    brand_id: Optional[str],
    category_id: Optional[str],
) -> None:
    if not brand_id and not category_id:
        return
    with get_connection() as conn:
        if brand_id:
            brand = conn.execute(
                "SELECT id FROM brands WHERE id = %s",
                (brand_id,),
            ).fetchone()
            if not brand:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid brand_id",
                )
        if category_id:
            category = conn.execute(
                "SELECT id FROM categories WHERE id = %s",
                (category_id,),
            ).fetchone()
            if not category:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid category_id",
                )


@router.get("")
def list_products(
    page: int = 1,
    per_page: int = 24,
    category: Optional[str] = None,
    brand: Optional[str] = None,
):
    offset = max(page - 1, 0) * per_page
    conditions = ["status = 'active'"]
    params: list = []
    if category:
        conditions.append("category_id = %s")
        params.append(category)
    if brand:
        conditions.append("brand_id = %s")
        params.append(brand)

    where_clause = " AND ".join(conditions)
    params.extend([per_page, offset])

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
              id,
              name,
              slug,
              brand_id,
              category_id,
              price,
              original_price,
              status,
              is_featured
            FROM products
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "slug": row[2],
            "brand_id": str(row[3]) if row[3] else None,
            "category_id": str(row[4]) if row[4] else None,
            "price": str(row[5]),
            "original_price": str(row[6]) if row[6] else None,
            "status": row[7],
            "is_featured": row[8],
        }
        for row in rows
    ]


@router.get("/{slug}")
def get_product(slug: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              id,
              name,
              slug,
              brand_id,
              category_id,
              price,
              original_price,
              specs,
              status,
              is_featured,
              meta_title,
              meta_description
            FROM products
            WHERE slug = %s AND status = 'active'
            """,
            (slug,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Product not found")

        images = conn.execute(
            """
            SELECT image_set_id, url, size, sort_order, is_primary
            FROM product_images
            WHERE product_id = %s
            ORDER BY is_primary DESC, sort_order ASC
            """,
            (row[0],),
        ).fetchall()

    return {
        "id": str(row[0]),
        "name": row[1],
        "slug": row[2],
        "brand_id": str(row[3]) if row[3] else None,
        "category_id": str(row[4]) if row[4] else None,
        "price": str(row[5]),
        "original_price": str(row[6]) if row[6] else None,
        "specs": row[7],
        "status": row[8],
        "is_featured": row[9],
        "meta_title": row[10],
        "meta_description": row[11],
        "images": [
            {
                "image_set_id": str(img[0]),
                "url": img[1],
                "size": img[2],
                "sort_order": img[3],
                "is_primary": img[4],
            }
            for img in images
        ],
    }


@router.post("")
def create_product(payload: ProductRequest, user=Depends(require_admin)):
    _validate_specs(payload.category_id, payload.specs)
    _validate_relations(payload.brand_id, payload.category_id)
    product_slug = payload.slug or slugify(payload.name)
    with get_connection() as conn:
        try:
            row = conn.execute(
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
                  is_featured,
                  meta_title,
                  meta_description
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    payload.name,
                    product_slug,
                    payload.brand_id,
                    payload.category_id,
                    payload.price,
                    payload.original_price,
                    Json(payload.specs) if payload.specs is not None else None,
                    payload.status,
                    payload.is_featured,
                    payload.meta_title,
                    payload.meta_description,
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="Product slug exists",
            ) from exc

    return {"id": str(row[0])}


@router.put("/{product_id}")
def update_product(
    product_id: str,
    payload: ProductRequest,
    user=Depends(require_admin),
):
    _validate_specs(payload.category_id, payload.specs)
    _validate_relations(payload.brand_id, payload.category_id)
    product_slug = payload.slug or slugify(payload.name)
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT price FROM products WHERE id = %s",
            (product_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Product not found")

        try:
            conn.execute(
                """
                UPDATE products
                SET
                  name = %s,
                  slug = %s,
                  brand_id = %s,
                  category_id = %s,
                  price = %s,
                  original_price = %s,
                  specs = %s,
                  status = %s,
                  is_featured = %s,
                  meta_title = %s,
                  meta_description = %s,
                  updated_at = NOW()
                WHERE id = %s
                """,
                (
                    payload.name,
                    product_slug,
                    payload.brand_id,
                    payload.category_id,
                    payload.price,
                    payload.original_price,
                    Json(payload.specs) if payload.specs is not None else None,
                    payload.status,
                    payload.is_featured,
                    payload.meta_title,
                    payload.meta_description,
                    product_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="Product slug exists",
            ) from exc

        if existing[0] != payload.price:
            conn.execute(
                """
                INSERT INTO product_price_history (
                  product_id,
                  old_price,
                  new_price
                )
                VALUES (%s, %s, %s)
                """,
                (product_id, existing[0], payload.price),
            )

    return {"status": "updated"}


@router.delete("/{product_id}")
def delete_product(product_id: str, user=Depends(require_admin)):
    with get_connection() as conn:
        updated = conn.execute(
            """
            UPDATE products
            SET status = 'discontinued', updated_at = NOW()
            WHERE id = %s
            """,
            (product_id,),
        )

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"status": "discontinued"}


@router.patch("/{product_id}/status")
def update_status(
    product_id: str,
    payload: ProductStatusRequest,
    user=Depends(require_admin),
):
    with get_connection() as conn:
        updated = conn.execute(
            """
            UPDATE products
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (payload.status, product_id),
        )
    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"status": "updated"}


@router.get("/{product_id}/price-history")
def price_history(product_id: str, user=Depends(require_admin)):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT old_price, new_price, changed_at
            FROM product_price_history
            WHERE product_id = %s
            ORDER BY changed_at DESC
            LIMIT 12
            """,
            (product_id,),
        ).fetchall()
    return [
        {
            "old_price": str(row[0]),
            "new_price": str(row[1]),
            "changed_at": row[2],
        }
        for row in rows
    ]


@admin_router.get("")
def admin_products(
    page: int = 1,
    per_page: int = 24,
    status: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    user=Depends(require_admin),
):
    offset = max(page - 1, 0) * per_page
    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if category:
        conditions.append("category_id = %s")
        params.append(category)
    if brand:
        conditions.append("brand_id = %s")
        params.append(brand)

    where_clause = " AND ".join(conditions)
    where_sql = f"WHERE {where_clause}" if where_clause else ""
    params.extend([per_page, offset])

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
              id,
              name,
              slug,
              brand_id,
              category_id,
              price,
              original_price,
              status,
              is_featured
            FROM products
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "slug": row[2],
            "brand_id": str(row[3]) if row[3] else None,
            "category_id": str(row[4]) if row[4] else None,
            "price": str(row[5]),
            "original_price": str(row[6]) if row[6] else None,
            "status": row[7],
            "is_featured": row[8],
        }
        for row in rows
    ]


@router.post("/{product_id}/images")
def upload_product_images(
    product_id: str,
    file: UploadFile = File(...),
    user=Depends(require_admin),
):
    if file.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=400, detail="Only JPG/PNG supported")

    content = file.file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")

    image_set_id = generate_image_set_id()
    sizes = {
        "thumbnail": "image/jpeg",
        "medium": "image/jpeg",
        "large": "image/jpeg",
    }
    uploads = []

    for size in sizes:
        resized = resize_image(content, size)
        key = f"products/{product_id}/{image_set_id}/{size}.jpg"
        url = upload_bytes(resized, key, "image/jpeg")
        uploads.append((size, key, url))

    with get_connection() as conn:
        product_row = conn.execute(
            "SELECT id FROM products WHERE id = %s",
            (product_id,),
        ).fetchone()
        if not product_row:
            raise HTTPException(status_code=404, detail="Product not found")

        existing = conn.execute(
            "SELECT COUNT(*) FROM product_images WHERE product_id = %s",
            (product_id,),
        ).fetchone()
        is_primary = existing[0] == 0
        sort_order = existing[0] + 1

        for size, key, url in uploads:
            conn.execute(
                """
                INSERT INTO product_images (
                  product_id,
                  image_set_id,
                  size,
                  storage_key,
                  url,
                  sort_order,
                  is_primary
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    product_id,
                    image_set_id,
                    size,
                    key,
                    url,
                    sort_order,
                    is_primary,
                ),
            )

    return {"image_set_id": image_set_id, "primary": is_primary}


@router.patch("/{product_id}/images/{image_set_id}/primary")
def set_primary_image(
    product_id: str,
    image_set_id: str,
    user=Depends(require_admin),
):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE product_images
            SET is_primary = FALSE
            WHERE product_id = %s
            """,
            (product_id,),
        )
        updated = conn.execute(
            """
            UPDATE product_images
            SET is_primary = TRUE
            WHERE product_id = %s AND image_set_id = %s
            """,
            (product_id, image_set_id),
        )

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Image set not found")

    return {"status": "updated"}


@router.delete("/{product_id}/images/{image_set_id}")
def delete_image_set(
    product_id: str, image_set_id: str, user=Depends(require_admin)
):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT storage_key
            FROM product_images
            WHERE product_id = %s AND image_set_id = %s
            """,
            (product_id, image_set_id),
        ).fetchall()

        deleted = conn.execute(
            """
            DELETE FROM product_images
            WHERE product_id = %s AND image_set_id = %s
            """,
            (product_id, image_set_id),
        )

    if deleted.rowcount == 0:
        raise HTTPException(status_code=404, detail="Image set not found")

    delete_objects([row[0] for row in rows])
    return {"status": "deleted"}
