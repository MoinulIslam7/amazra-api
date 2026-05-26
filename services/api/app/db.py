from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def init_pool() -> None:
    global _pool
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    _pool = ConnectionPool(
        conninfo=settings.database_url,
        open=True,
        kwargs={"autocommit": True},
    )


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.close()
        _pool = None


@contextmanager
def get_connection():
    if not _pool:
        raise RuntimeError("Database pool not initialized")
    with _pool.connection() as conn:
        yield conn
