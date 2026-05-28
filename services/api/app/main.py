from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .auth import router as auth_router
from .brands import router as brands_router
from .categories import admin_router as admin_categories_router
from .categories import router as categories_router
from .config import get_settings
from .db import close_pool, init_pool
from .imports import router as imports_router
from .inventory import router as inventory_router
from .products import admin_router as admin_products_router
from .products import router as products_router
from .redis_client import init_redis
from .search import router as search_router
from .search_client import init_search
from .users import router as users_router


app = FastAPI(title="Amazra API", version="0.1.0")

settings = get_settings()
if not settings.s3_bucket:
    local_root = Path(settings.local_storage_path).expanduser().resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(local_root)), name="media")


@app.on_event("startup")
def startup() -> None:
    init_pool()
    init_redis()
    init_search()


@app.on_event("shutdown")
def shutdown() -> None:
    close_pool()


@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}


app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(categories_router, prefix="/api/v1")
app.include_router(admin_categories_router, prefix="/api/v1")
app.include_router(brands_router, prefix="/api/v1")
app.include_router(products_router, prefix="/api/v1")
app.include_router(admin_products_router, prefix="/api/v1")
app.include_router(imports_router, prefix="/api/v1")
app.include_router(inventory_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
