import io
import uuid
from pathlib import Path
from typing import Literal

import boto3
from PIL import Image

from .config import get_settings


def _s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        endpoint_url=settings.s3_endpoint_url,
    )


def _use_s3() -> bool:
    settings = get_settings()
    return bool(settings.s3_bucket)


def _local_root() -> Path:
    settings = get_settings()
    root = Path(settings.local_storage_path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_url(key: str) -> str:
    settings = get_settings()
    base_url = settings.public_base_url.rstrip("/")
    return f"{base_url}/media/{key}"


def upload_bytes(content: bytes, key: str, content_type: str) -> str:
    settings = get_settings()
    if _use_s3():
        if not settings.s3_access_key_id or not settings.s3_secret_access_key:
            raise RuntimeError(
                "S3 credentials are required when S3_BUCKET is set"
            )

        client = _s3_client()
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )

        base_url = settings.cdn_base_url or ""
        if base_url:
            return f"{base_url.rstrip('/')}/{key}"
        return f"s3://{settings.s3_bucket}/{key}"

    root = _local_root()
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _local_url(key)


def delete_objects(keys: list[str]) -> None:
    settings = get_settings()
    if not keys:
        return
    if _use_s3():
        client = _s3_client()
        client.delete_objects(
            Bucket=settings.s3_bucket,
            Delete={"Objects": [{"Key": key} for key in keys]},
        )
        return

    root = _local_root()
    for key in keys:
        path = root / key
        if path.exists():
            path.unlink()


def download_bytes(key: str) -> bytes:
    settings = get_settings()
    if _use_s3():
        client = _s3_client()
        response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        return response["Body"].read()

    root = _local_root()
    path = root / key
    if not path.exists():
        raise FileNotFoundError(f"Local file not found: {key}")
    return path.read_bytes()


def resize_image(
    content: bytes,
    size: Literal["thumbnail", "medium", "large"],
) -> bytes:
    target = {"thumbnail": 150, "medium": 400, "large": 800}[size]
    with Image.open(io.BytesIO(content)) as img:
        img = img.convert("RGB")
        img.thumbnail((target, target))
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85)
        return output.getvalue()


def generate_image_set_id() -> str:
    return str(uuid.uuid4())
