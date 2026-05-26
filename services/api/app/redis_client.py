import redis

from .config import get_settings

_client: redis.Redis | None = None


def init_redis() -> None:
    global _client
    settings = get_settings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required")
    _client = redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> redis.Redis:
    if not _client:
        raise RuntimeError("Redis client not initialized")
    return _client
