from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .db import get_connection
from .deps import require_admin
from .storage import upload_bytes
from .utils import slugify

router = APIRouter(prefix="/brands", tags=["brands"])


class BrandRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    slug: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool = True


@router.get("")
def list_brands(page: int = 1, per_page: int = 24):
    offset = max(page - 1, 0) * per_page
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, slug, logo_url, is_active
            FROM brands
            WHERE is_active = TRUE
            ORDER BY name ASC
            LIMIT %s OFFSET %s
            """,
            (per_page, offset),
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "slug": row[2],
            "logo_url": row[3],
            "is_active": row[4],
        }
        for row in rows
    ]


@router.get("/{slug}")
def get_brand(slug: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, slug, logo_url, is_active
            FROM brands
            WHERE slug = %s
            """,
            (slug,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Brand not found")

    return {
        "id": str(row[0]),
        "name": row[1],
        "slug": row[2],
        "logo_url": row[3],
        "is_active": row[4],
    }


@router.post("")
def create_brand(payload: BrandRequest, user=Depends(require_admin)):
    brand_slug = payload.slug or slugify(payload.name)
    with get_connection() as conn:
        try:
            row = conn.execute(
                """
                INSERT INTO brands (name, slug, logo_url, is_active)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    payload.name,
                    brand_slug,
                    payload.logo_url,
                    payload.is_active,
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="Brand slug exists",
            ) from exc

    return {"id": str(row[0])}


@router.put("/{brand_id}")
def update_brand(
    brand_id: str,
    payload: BrandRequest,
    user=Depends(require_admin),
):
    brand_slug = payload.slug or slugify(payload.name)
    with get_connection() as conn:
        try:
            updated = conn.execute(
                """
                UPDATE brands
                SET
                  name = %s,
                  slug = %s,
                  logo_url = %s,
                  is_active = %s,
                  updated_at = NOW()
                WHERE id = %s
                """,
                (
                    payload.name,
                    brand_slug,
                    payload.logo_url,
                    payload.is_active,
                    brand_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="Brand slug exists",
            ) from exc

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Brand not found")

    return {"status": "updated"}


@router.post("/{brand_id}/logo")
def upload_brand_logo(
    brand_id: str,
    file: UploadFile = File(...),
    user=Depends(require_admin),
):
    if file.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=400, detail="Only JPG/PNG supported")

    content = file.file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")

    key = f"brands/{brand_id}/logo.jpg"
    url = upload_bytes(content, key, "image/jpeg")

    with get_connection() as conn:
        updated = conn.execute(
            (
                "UPDATE brands SET logo_url = %s, updated_at = NOW() "
                "WHERE id = %s"
            ),
            (url, brand_id),
        )

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Brand not found")

    return {"logo_url": url}
