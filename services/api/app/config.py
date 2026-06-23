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
    elasticsearch_url: str
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
    search_reindex_queue_name: str
    low_stock_queue_name: str
    order_placed_queue_name: str
    refund_queue_name: str
    search_index_alias: str
    search_index_prefix: str
    local_storage_path: str
    public_base_url: str
    # SSLCOMMERZ
    sslcommerz_store_id: str | None
    sslcommerz_store_pass: str | None
    sslcommerz_is_sandbox: bool
    # bKash tokenized checkout
    bkash_base_url: str | None
    bkash_username: str | None
    bkash_password: str | None
    bkash_app_key: str | None
    bkash_app_secret: str | None
    # Nagad
    nagad_base_url: str | None
    nagad_merchant_id: str | None
    nagad_merchant_private_key: str | None  # PEM (newlines as \n in env)
    nagad_public_key: str | None            # Nagad's RSA public key PEM
    # COD
    cod_max_order_amount: str  # BDT ceiling; compared as Decimal at runtime
    # Payment event queues
    payment_confirmed_queue_name: str
    payment_failed_queue_name: str
    # ── Phase 5: Delivery ─────────────────────────────────────────────────────
    # Pathao courier
    pathao_base_url: str
    pathao_client_id: str | None
    pathao_client_secret: str | None
    pathao_username: str | None
    pathao_password: str | None
    pathao_store_id: int | None
    pathao_default_city_id: int | None
    pathao_default_zone_id: int | None
    # Steadfast courier
    steadfast_base_url: str
    steadfast_api_key: str | None
    steadfast_secret_key: str | None
    # ── Phase 5: Notifications ────────────────────────────────────────────────
    # SMS via Twilio
    sms_provider: str           # twilio | generic_http | none
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_from_number: str | None
    # SMS via generic HTTP (SSL Wireless and similar BD providers)
    generic_sms_url: str | None
    generic_sms_api_key: str | None
    generic_sms_sender_id: str | None
    # Email via SendGrid
    sendgrid_api_key: str | None
    sendgrid_from_email: str | None
    sendgrid_from_name: str
    # Notification event queue (consumed by notification_worker)
    notification_events_queue_name: str


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
        elasticsearch_url=os.getenv("ELASTICSEARCH_URL", ""),
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
        search_reindex_queue_name=os.getenv(
            "SEARCH_REINDEX_QUEUE_NAME", "search_reindex"
        ),
        low_stock_queue_name=os.getenv(
            "LOW_STOCK_QUEUE_NAME", "inventory_low_stock"
        ),
        order_placed_queue_name=os.getenv(
            "ORDER_PLACED_QUEUE_NAME", "order_placed"
        ),
        refund_queue_name=os.getenv("REFUND_QUEUE_NAME", "payment_refunds"),
        search_index_alias=os.getenv("SEARCH_INDEX_ALIAS", "products"),
        search_index_prefix=os.getenv("SEARCH_INDEX_PREFIX", "products"),
        local_storage_path=os.getenv("LOCAL_STORAGE_PATH", "storage"),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8001"),
        # payment gateways
        sslcommerz_store_id=os.getenv("SSLCOMMERZ_STORE_ID"),
        sslcommerz_store_pass=os.getenv("SSLCOMMERZ_STORE_PASS"),
        sslcommerz_is_sandbox=os.getenv("SSLCOMMERZ_IS_SANDBOX", "true").lower() != "false",
        bkash_base_url=os.getenv(
            "BKASH_BASE_URL", "https://tokenized.sandbox.bka.sh/v1.2.0-beta"
        ),
        bkash_username=os.getenv("BKASH_USERNAME"),
        bkash_password=os.getenv("BKASH_PASSWORD"),
        bkash_app_key=os.getenv("BKASH_APP_KEY"),
        bkash_app_secret=os.getenv("BKASH_APP_SECRET"),
        nagad_base_url=os.getenv(
            "NAGAD_BASE_URL", "https://sandbox.mynagad.com:10080/remote-payment-gateway-1.0"
        ),
        nagad_merchant_id=os.getenv("NAGAD_MERCHANT_ID"),
        nagad_merchant_private_key=_normalize_pem(os.getenv("NAGAD_MERCHANT_PRIVATE_KEY")),
        nagad_public_key=_normalize_pem(os.getenv("NAGAD_PUBLIC_KEY")),
        cod_max_order_amount=os.getenv("COD_MAX_ORDER_AMOUNT", "50000"),
        payment_confirmed_queue_name=os.getenv(
            "PAYMENT_CONFIRMED_QUEUE_NAME", "payment_confirmed"
        ),
        payment_failed_queue_name=os.getenv(
            "PAYMENT_FAILED_QUEUE_NAME", "payment_failed"
        ),
        # Phase 5: Delivery
        pathao_base_url=os.getenv("PATHAO_BASE_URL", "https://hermes.pathao.com"),
        pathao_client_id=os.getenv("PATHAO_CLIENT_ID"),
        pathao_client_secret=os.getenv("PATHAO_CLIENT_SECRET"),
        pathao_username=os.getenv("PATHAO_USERNAME"),
        pathao_password=os.getenv("PATHAO_PASSWORD"),
        pathao_store_id=(
            int(os.getenv("PATHAO_STORE_ID"))
            if os.getenv("PATHAO_STORE_ID")
            else None
        ),
        pathao_default_city_id=(
            int(os.getenv("PATHAO_DEFAULT_CITY_ID"))
            if os.getenv("PATHAO_DEFAULT_CITY_ID")
            else None
        ),
        pathao_default_zone_id=(
            int(os.getenv("PATHAO_DEFAULT_ZONE_ID"))
            if os.getenv("PATHAO_DEFAULT_ZONE_ID")
            else None
        ),
        steadfast_base_url=os.getenv(
            "STEADFAST_BASE_URL", "https://portal.steadfast.com.bd/public/v1"
        ),
        steadfast_api_key=os.getenv("STEADFAST_API_KEY"),
        steadfast_secret_key=os.getenv("STEADFAST_SECRET_KEY"),
        # Phase 5: Notifications
        sms_provider=os.getenv("SMS_PROVIDER", "none"),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
        twilio_from_number=os.getenv("TWILIO_FROM_NUMBER"),
        generic_sms_url=os.getenv("GENERIC_SMS_URL"),
        generic_sms_api_key=os.getenv("GENERIC_SMS_API_KEY"),
        generic_sms_sender_id=os.getenv("GENERIC_SMS_SENDER_ID"),
        sendgrid_api_key=os.getenv("SENDGRID_API_KEY"),
        sendgrid_from_email=os.getenv("SENDGRID_FROM_EMAIL"),
        sendgrid_from_name=os.getenv("SENDGRID_FROM_NAME", "Amazra"),
        notification_events_queue_name=os.getenv(
            "NOTIFICATION_EVENTS_QUEUE_NAME", "notification_events"
        ),
    )
