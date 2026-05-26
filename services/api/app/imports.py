import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .config import get_settings
from .db import get_connection
from .deps import require_admin
from .queue import publish_message
from .storage import upload_bytes

router = APIRouter(prefix="/products/import", tags=["imports"])

REQUIRED_HEADERS = [
    "name",
    "slug",
    "brand_id",
    "category_id",
    "price",
    "original_price",
    "status",
    "is_featured",
    "specs_json",
]


@router.post("")
def create_import_job(
    file: UploadFile = File(...),
    user=Depends(require_admin),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="CSV file required")

    content = file.file.read()
    reader = csv.reader(io.StringIO(content.decode("utf-8")))
    headers = next(reader, None)
    if headers != REQUIRED_HEADERS:
        raise HTTPException(status_code=400, detail="Invalid CSV headers")

    job_id = str(uuid.uuid4())
    key = f"imports/{job_id}.csv"
    upload_bytes(content, key, "text/csv")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO product_import_jobs (id, status, file_key)
            VALUES (%s, %s, %s)
            """,
            (job_id, "pending", key),
        )

    publish_message(get_settings().import_queue_name, job_id)
    return {"job_id": job_id}


@router.get("/template", response_class=PlainTextResponse)
def download_template(user=Depends(require_admin)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(REQUIRED_HEADERS)
    headers = {
        "Content-Disposition": (
            "attachment; filename=product_import_template.csv"
        )
    }
    return PlainTextResponse(output.getvalue(), headers=headers)


@router.get("/{job_id}")
def get_import_job(job_id: str, user=Depends(require_admin)):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              id,
              status,
              total_rows,
              processed_rows,
              success_count,
              error_count
            FROM product_import_jobs
            WHERE id = %s
            """,
            (job_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        errors = conn.execute(
            """
            SELECT row_number, error_message
            FROM product_import_job_errors
            WHERE job_id = %s
            ORDER BY row_number ASC
            LIMIT 200
            """,
            (job_id,),
        ).fetchall()

    return {
        "job_id": str(row[0]),
        "status": row[1],
        "total_rows": row[2],
        "processed_rows": row[3],
        "success_count": row[4],
        "error_count": row[5],
        "errors": [{"row_number": e[0], "message": e[1]} for e in errors],
    }
