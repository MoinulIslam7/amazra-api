import os
from dataclasses import dataclass


def _read_key(value: str | None, path_value: str | None) -> str | None:
    if path_value:
        with open(path_value, "r", encoding="utf-8") as handle:
            return handle.read()
    return value


def _normalize_pem(value: str | None) -> str | None:
    if not value:
        return value
    return value.replace("\\n", "\n")


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_private_key: str
    jwt_public_key: str
    jwt_access_ttl_minutes: int
    jwt_refresh_ttl_days: int
    otp_ttl_seconds: int
    otp_rate_limit_per_hour: int
    s3_bucket: str | None
    s3_region: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None
    s3_endpoint_url: str | None
    cdn_base_url: str | None
    import_queue_name: str
    local_storage_path: str
    public_base_url: str


def get_settings() -> Settings:
    private_key = _normalize_pem(
        _read_key(
            os.getenv("JWT_PRIVATE_KEY"),
            os.getenv("JWT_PRIVATE_KEY_PATH"),
        )
    )
    public_key = _normalize_pem(
        _read_key(
            os.getenv("JWT_PUBLIC_KEY"),
            os.getenv("JWT_PUBLIC_KEY_PATH"),
        )
    )
    if not private_key or not public_key:
        raise RuntimeError("JWT_PRIVATE_KEY and JWT_PUBLIC_KEY are required")

    return Settings(
        database_url=os.getenv("DATABASE_URL", ""),
        redis_url=os.getenv("REDIS_URL", ""),
        jwt_private_key=private_key,
        jwt_public_key=public_key,
        jwt_access_ttl_minutes=int(os.getenv("JWT_ACCESS_TTL_MINUTES", "15")),
        jwt_refresh_ttl_days=int(os.getenv("JWT_REFRESH_TTL_DAYS", "30")),
        otp_ttl_seconds=int(os.getenv("OTP_TTL_SECONDS", "300")),
        otp_rate_limit_per_hour=int(os.getenv("OTP_RATE_LIMIT_PER_HOUR", "5")),
        s3_bucket=os.getenv("S3_BUCKET"),
        s3_region=os.getenv("S3_REGION"),
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        cdn_base_url=os.getenv("CDN_BASE_URL"),
        import_queue_name=os.getenv("IMPORT_QUEUE_NAME", "product_imports"),
        local_storage_path=os.getenv("LOCAL_STORAGE_PATH", "storage"),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8001"),
    )
