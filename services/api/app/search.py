import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg.types.json import Json

from .config import get_settings
from .db import get_connection
from .deps import require_admin
from .queue import publish_message
from .redis_client import get_redis
from .search_client import get_search_client
from .search_index import ensure_search_alias

router = APIRouter(prefix="/search", tags=["search"])

UUID_REGEX = re.compile(r"^[0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-")

FINDER_QUESTIONS = [
    {
        "id": "use_case",
        "label": "Primary use-case",
        "type": "single",
        "options": ["gaming", "business", "student", "creator"],
    },
    {
        "id": "budget_max",
        "label": "Maximum budget (BDT)",
        "type": "number",
        "min": 20000,
        "max": 400000,
        "step": 1000,
    },
    {
        "id": "os",
        "label": "Preferred operating system",
        "type": "single",
        "options": ["windows", "macos", "linux", "no_preference"],
    },
    {
        "id": "display_size",
        "label": "Display size",
        "type": "single",
        "options": ["13", "14", "15", "16", "17", "no_preference"],
    },
    {
        "id": "priority",
        "label": "Top priority",
        "type": "single",
        "options": ["battery", "performance", "portability"],
    },
]


class FinderRequest(BaseModel):
    use_case: str = Field(..., min_length=3, max_length=50)
    budget_max: int = Field(..., gt=0)
    budget_min: int = Field(0, ge=0)
    os: Optional[str] = None
    display_size: Optional[str] = None
    priority: Optional[str] = None


def _split_values(value: Optional[str]) -> list[str]:
    """Split comma-delimited query values into a clean list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _partition_values(values: list[str]) -> tuple[list[str], list[str]]:
    """Partition values into UUIDs and slugs/names."""
    ids = []
    slugs = []
    for value in values:
        if UUID_REGEX.match(value):
            ids.append(value)
        else:
            slugs.append(value)
    return ids, slugs


def _parse_specs(values: list[str]) -> dict[str, list[str]]:
    """Parse spec filters formatted as key:value."""
    specs: dict[str, list[str]] = {}
    for entry in values:
        if ":" not in entry:
            continue
        key, raw_value = entry.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or not value:
            continue
        specs.setdefault(key, []).append(value)
    return specs


def _build_search_query(
    query: Optional[str],
    brand_ids: list[str],
    brand_slugs: list[str],
    category_ids: list[str],
    category_slugs: list[str],
    min_price: Optional[float],
    max_price: Optional[float],
    in_stock: Optional[bool],
    specs: dict[str, list[str]],
) -> dict:
    """Build the Elasticsearch query with filters and boosting."""
    filters = [{"term": {"status": "active"}}]
    if brand_ids:
        filters.append({"terms": {"brand_id": brand_ids}})
    if brand_slugs:
        filters.append({"terms": {"brand_slug": brand_slugs}})
    if category_ids:
        filters.append({"terms": {"category_id": category_ids}})
    if category_slugs:
        filters.append({"terms": {"category_slug": category_slugs}})
    if min_price is not None or max_price is not None:
        price_filter: dict[str, float] = {}
        if min_price is not None:
            price_filter["gte"] = min_price
        if max_price is not None:
            price_filter["lte"] = max_price
        filters.append({"range": {"price": price_filter}})
    if in_stock is True:
        filters.append({"term": {"in_stock": True}})
    for key, values in specs.items():
        filters.append({"terms": {f"specs.{key}": values}})

    if query:
        must = [
            {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "name^3",
                        "brand^2",
                        "category",
                        "specs_text",
                    ],
                    "fuzziness": "AUTO",
                }
            }
        ]
    else:
        must = [{"match_all": {}}]

    return {"bool": {"must": must, "filter": filters}}


def _collect_facets(aggs: dict) -> dict:
    """Normalize aggregation buckets for API responses."""
    return {
        "brands": [
            {"key": bucket["key"], "count": bucket["doc_count"]}
            for bucket in aggs.get("brands", {}).get("buckets", [])
        ],
        "price_histogram": [
            {
                "key": bucket["key"],
                "count": bucket["doc_count"],
            }
            for bucket in aggs.get("price_histogram", {}).get("buckets", [])
        ],
        "spec_values": [
            {"key": bucket["key"], "count": bucket["doc_count"]}
            for bucket in aggs.get("spec_values", {}).get("buckets", [])
        ],
    }


def _store_search_query(query: Optional[str], filters: dict, total: int) -> None:
    """Persist search analytics in PostgreSQL."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO search_queries (query, filters, result_count)
            VALUES (%s, %s, %s)
            """,
            (query, Json(filters), total),
        )


def _finder_should_clauses(payload: FinderRequest) -> list[dict]:
    """Generate boosting clauses for laptop finder recommendations."""
    should = []
    use_case = payload.use_case.lower()
    if use_case == "gaming":
        should.extend(
            [
                {"match": {"name": {"query": "gaming", "boost": 3}}},
                {"match": {"specs_text": {"query": "rtx gtx gpu", "boost": 2}}},
                {"match": {"specs_text": {"query": "graphics", "boost": 2}}},
            ]
        )
    elif use_case == "business":
        should.append(
            {"match": {"specs_text": {"query": "business", "boost": 2}}}
        )
    elif use_case == "creator":
        should.append(
            {"match": {"specs_text": {"query": "creator studio", "boost": 2}}}
        )

    if payload.os and payload.os != "no_preference":
        should.append(
            {"match": {"specs_text": {"query": payload.os, "boost": 2}}}
        )

    if payload.display_size and payload.display_size != "no_preference":
        should.append(
            {
                "match": {
                    "specs_text": {
                        "query": payload.display_size,
                        "boost": 1.5,
                    }
                }
            }
        )

    if payload.priority == "battery":
        should.append(
            {"match": {"specs_text": {"query": "battery wh", "boost": 2}}}
        )
    elif payload.priority == "performance":
        should.append(
            {
                "match": {
                    "specs_text": {
                        "query": "i7 i9 ryzen 7 ryzen 9 rtx",
                        "boost": 2,
                    }
                }
            }
        )
    elif payload.priority == "portability":
        should.append(
            {"match": {"specs_text": {"query": "thin light", "boost": 2}}}
        )
    return should


def _finder_explanation(payload: FinderRequest, source: dict) -> list[str]:
    """Explain which criteria matched a laptop recommendation."""
    explanations = []
    text = f"{source.get('name', '')} {source.get('specs_text', '')}".lower()

    if payload.use_case and payload.use_case.lower() in text:
        explanations.append(f"Matches use-case: {payload.use_case}")

    if payload.os and payload.os != "no_preference" and payload.os in text:
        explanations.append(f"OS preference: {payload.os}")

    if (
        payload.display_size
        and payload.display_size != "no_preference"
        and payload.display_size in text
    ):
        explanations.append(f"Display size: {payload.display_size}\"")

    if payload.priority == "battery" and "battery" in text:
        explanations.append("Battery-focused specs")
    if payload.priority == "performance" and any(
        term in text for term in ["i7", "i9", "ryzen", "rtx", "gtx"]
    ):
        explanations.append("Performance-focused specs")
    if payload.priority == "portability" and any(
        term in text for term in ["thin", "light", "ultrabook"]
    ):
        explanations.append("Portable form factor")

    return explanations


@router.get("")
def search_products(
    q: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    in_stock: Optional[bool] = None,
    sort: Optional[str] = None,
    page: int = 1,
    per_page: int = 24,
    specs: Optional[list[str]] = Query(None),
):
    """Search products with filters, facets, and sorting."""
    alias = ensure_search_alias()
    client = get_search_client()
    brand_ids, brand_slugs = _partition_values(_split_values(brand))
    category_ids, category_slugs = _partition_values(_split_values(category))
    specs_filters = _parse_specs(specs or [])

    query = _build_search_query(
        query=q,
        brand_ids=brand_ids,
        brand_slugs=brand_slugs,
        category_ids=category_ids,
        category_slugs=category_slugs,
        min_price=min_price,
        max_price=max_price,
        in_stock=in_stock,
        specs=specs_filters,
    )

    sort_clause = None
    if sort == "price-asc":
        sort_clause = [{"price": "asc"}]
    elif sort == "price-desc":
        sort_clause = [{"price": "desc"}]
    elif sort == "newest":
        sort_clause = [{"created_at": "desc"}]
    elif sort == "popularity":
        sort_clause = [{"is_featured": "desc"}, {"stock": "desc"}]

    body = {
        "query": query,
        "from": max(page - 1, 0) * per_page,
        "size": per_page,
        "track_total_hits": True,
        "aggs": {
            "brands": {"terms": {"field": "brand.keyword", "size": 30}},
            "price_histogram": {"histogram": {"field": "price", "interval": 5000}},
            "spec_values": {"terms": {"field": "specs", "size": 40}},
        },
    }
    if sort_clause:
        body["sort"] = sort_clause

    response = client.search(index=alias, body=body)
    hits = response.get("hits", {})
    total = hits.get("total", {}).get("value", 0)
    items = []
    for hit in hits.get("hits", []):
        source = hit.get("_source", {})
        items.append(
            {
                "id": source.get("id"),
                "name": source.get("name"),
                "slug": source.get("slug"),
                "brand": source.get("brand"),
                "category": source.get("category"),
                "price": source.get("price"),
                "original_price": source.get("original_price"),
                "in_stock": source.get("in_stock"),
                "stock": source.get("stock"),
            }
        )

    _store_search_query(
        q,
        {
            "brand": brand,
            "category": category,
            "min_price": min_price,
            "max_price": max_price,
            "in_stock": in_stock,
            "specs": specs_filters,
            "sort": sort,
        },
        total,
    )

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": items,
        "facets": _collect_facets(response.get("aggregations", {})),
    }


@router.get("/suggest")
def suggest(q: str):
    """Return fast autocomplete suggestions backed by Redis cache."""
    if len(q.strip()) < 2:
        return []

    redis_client = get_redis()
    cache_key = f"search:suggest:{q.strip().lower()}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    alias = ensure_search_alias()
    client = get_search_client()
    response = client.search(
        index=alias,
        body={
            "size": 8,
            "query": {
                "multi_match": {
                    "query": q,
                    "type": "bool_prefix",
                    "fields": [
                        "name.autocomplete",
                        "brand.autocomplete",
                        "category.autocomplete",
                    ],
                }
            },
            "_source": ["name", "slug"],
        },
    )
    suggestions = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        suggestions.append({"name": source.get("name"), "slug": source.get("slug")})

    redis_client.setex(cache_key, 60, json.dumps(suggestions))
    return suggestions


@router.post("/index/all")
def reindex_all(user=Depends(require_admin)):
    """Queue a full reindex of all products."""
    job_id = str(uuid.uuid4())
    settings = get_settings()
    publish_message(settings.search_reindex_queue_name, job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/finder/questions")
def finder_questions():
    """Return the laptop finder questionnaire schema."""
    return FINDER_QUESTIONS


@router.post("/finder/results")
def finder_results(payload: FinderRequest):
    """Return laptop recommendations based on finder answers."""
    if payload.budget_min > payload.budget_max:
        raise HTTPException(
            status_code=400, detail="budget_min cannot exceed budget_max"
        )

    alias = ensure_search_alias()
    client = get_search_client()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT slug
            FROM categories
            WHERE slug ILIKE '%laptop%'
            """
        ).fetchall()
    laptop_slugs = [row[0] for row in rows]
    filters = [
        {"term": {"status": "active"}},
        {
            "range": {
                "price": {"gte": payload.budget_min, "lte": payload.budget_max}
            }
        },
    ]
    if laptop_slugs:
        filters.append({"terms": {"category_slug": laptop_slugs}})

    query = {
        "bool": {
            "must": [{"match_all": {}}],
            "filter": filters,
            "should": _finder_should_clauses(payload),
        }
    }

    response = client.search(
        index=alias,
        body={
            "query": query,
            "size": 24,
            "_source": [
                "id",
                "name",
                "slug",
                "brand",
                "category",
                "price",
                "specs_text",
            ],
        },
    )

    results = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        explanation = _finder_explanation(payload, source)
        results.append(
            {
                "id": source.get("id"),
                "name": source.get("name"),
                "slug": source.get("slug"),
                "brand": source.get("brand"),
                "category": source.get("category"),
                "price": source.get("price"),
                "match_score": len(explanation),
                "match_explanation": explanation,
                "score": hit.get("_score", 0),
            }
        )

    results.sort(key=lambda item: (-item["match_score"], -item["score"]))
    return results[:8]
