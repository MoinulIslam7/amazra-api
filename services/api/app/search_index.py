from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from elasticsearch import helpers
from elasticsearch.exceptions import NotFoundError

from .config import get_settings
from .db import get_connection
from .search_client import get_search_client


def _index_settings() -> dict[str, Any]:
    """Build the Elasticsearch settings + mappings for the product index."""
    return {
        "settings": {
            "analysis": {
                "tokenizer": {
                    "edge_ngram_tokenizer": {
                        "type": "edge_ngram",
                        "min_gram": 2,
                        "max_gram": 20,
                        "token_chars": ["letter", "digit"],
                    }
                },
                "analyzer": {
                    "autocomplete": {
                        "type": "custom",
                        "tokenizer": "edge_ngram_tokenizer",
                        "filter": ["lowercase"],
                    },
                    "autocomplete_search": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase"],
                    },
                },
            }
        },
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "slug": {"type": "keyword"},
                "name": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword"},
                        "autocomplete": {
                            "type": "text",
                            "analyzer": "autocomplete",
                            "search_analyzer": "autocomplete_search",
                        },
                    },
                },
                "brand": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword"},
                        "autocomplete": {
                            "type": "text",
                            "analyzer": "autocomplete",
                            "search_analyzer": "autocomplete_search",
                        },
                    },
                },
                "brand_id": {"type": "keyword"},
                "brand_slug": {"type": "keyword"},
                "category": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword"},
                        "autocomplete": {
                            "type": "text",
                            "analyzer": "autocomplete",
                            "search_analyzer": "autocomplete_search",
                        },
                    },
                },
                "category_id": {"type": "keyword"},
                "category_slug": {"type": "keyword"},
                "specs": {"type": "flattened"},
                "specs_text": {"type": "text"},
                "price": {"type": "double"},
                "original_price": {"type": "double"},
                "status": {"type": "keyword"},
                "is_featured": {"type": "boolean"},
                "in_stock": {"type": "boolean"},
                "stock": {"type": "integer"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            }
        },
    }


def _new_index_name() -> str:
    """Create a timestamped index name for zero-downtime reindexing."""
    settings = get_settings()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{settings.search_index_prefix}-{timestamp}"


def _ensure_index(index_name: str) -> None:
    """Create the Elasticsearch index if it does not exist."""
    client = get_search_client()
    if client.indices.exists(index=index_name):
        return
    client.indices.create(index=index_name, body=_index_settings())


def _ensure_alias() -> str:
    """Ensure the search alias exists and points to a valid index."""
    settings = get_settings()
    alias = settings.search_index_alias
    client = get_search_client()
    if client.indices.exists_alias(name=alias):
        return alias

    index_name = _new_index_name()
    _ensure_index(index_name)
    client.indices.put_alias(index=index_name, name=alias)
    return alias


def ensure_search_alias() -> str:
    """Public wrapper to guarantee the search alias exists."""
    return _ensure_alias()


def _swap_alias(alias: str, new_index: str) -> None:
    """Swap the alias to point at a new index with minimal downtime."""
    client = get_search_client()
    actions = []
    if client.indices.exists_alias(name=alias):
        for index_name in client.indices.get_alias(name=alias).keys():
            actions.append({"remove": {"index": index_name, "alias": alias}})
    actions.append({"add": {"index": new_index, "alias": alias}})
    client.indices.update_aliases(body={"actions": actions})


def _flatten_specs(specs: dict | None) -> str:
    """Flatten product specs into a searchable text string."""
    if not specs:
        return ""
    parts = []
    for key, value in specs.items():
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = [value]
        for item in values:
            parts.append(f"{key} {item}")
    return " ".join(str(part) for part in parts)


def _decimal_to_float(value: Decimal | None) -> float | None:
    """Convert Decimals to float for Elasticsearch indexing."""
    if value is None:
        return None
    return float(value)


def _build_document(row: tuple, stock: int) -> dict[str, Any]:
    """Build a searchable document from a product row."""
    (
        product_id,
        name,
        slug,
        price,
        original_price,
        specs,
        status,
        is_featured,
        created_at,
        updated_at,
        brand_id,
        brand_name,
        brand_slug,
        category_id,
        category_name,
        category_slug,
    ) = row
    specs_dict = specs or {}
    return {
        "id": str(product_id),
        "name": name,
        "slug": slug,
        "brand": brand_name,
        "brand_id": str(brand_id) if brand_id else None,
        "brand_slug": brand_slug,
        "category": category_name,
        "category_id": str(category_id) if category_id else None,
        "category_slug": category_slug,
        "specs": specs_dict,
        "specs_text": _flatten_specs(specs_dict),
        "price": _decimal_to_float(price),
        "original_price": _decimal_to_float(original_price),
        "status": status,
        "is_featured": is_featured,
        "in_stock": stock > 0,
        "stock": stock,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _fetch_product_row(conn, product_id: str) -> tuple | None:
    """Fetch a single product row with brand/category metadata."""
    return conn.execute(
        """
        SELECT
          p.id,
          p.name,
          p.slug,
          p.price,
          p.original_price,
          p.specs,
          p.status,
          p.is_featured,
          p.created_at,
          p.updated_at,
          b.id,
          b.name,
          b.slug,
          c.id,
          c.name,
          c.slug
        FROM products p
        LEFT JOIN brands b ON p.brand_id = b.id
        LEFT JOIN categories c ON p.category_id = c.id
        WHERE p.id = %s
        """,
        (product_id,),
    ).fetchone()


def _fetch_stock(conn, product_id: str) -> int:
    """Calculate total available stock for a product."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity - reserved_qty), 0)
        FROM inventory
        WHERE product_id = %s
        """,
        (product_id,),
    ).fetchone()
    return int(row[0] or 0)


def upsert_product_document(product_id: str) -> None:
    """Create or update the Elasticsearch document for a product."""
    alias = _ensure_alias()
    with get_connection() as conn:
        row = _fetch_product_row(conn, product_id)
        if not row:
            return
        stock = _fetch_stock(conn, product_id)
        document = _build_document(row, stock)
    client = get_search_client()
    client.index(index=alias, id=str(product_id), document=document)


def delete_product_document(product_id: str) -> None:
    """Remove a product document from Elasticsearch."""
    client = get_search_client()
    settings = get_settings()
    alias = settings.search_index_alias
    if not client.indices.exists_alias(name=alias):
        return
    try:
        client.delete(index=alias, id=str(product_id))
    except NotFoundError:
        return


def update_product_stock(product_id: str) -> None:
    """Update stock fields in Elasticsearch when inventory changes."""
    client = get_search_client()
    alias = _ensure_alias()
    with get_connection() as conn:
        stock = _fetch_stock(conn, product_id)
    try:
        client.update(
            index=alias,
            id=str(product_id),
            body={"doc": {"in_stock": stock > 0, "stock": stock}},
        )
    except NotFoundError:
        upsert_product_document(product_id)


def reindex_all_products() -> str:
    """Rebuild the entire product index and swap the alias."""
    client = get_search_client()
    new_index = _new_index_name()
    _ensure_index(new_index)
    with get_connection() as conn:
        product_rows = conn.execute(
            """
            SELECT
              p.id,
              p.name,
              p.slug,
              p.price,
              p.original_price,
              p.specs,
              p.status,
              p.is_featured,
              p.created_at,
              p.updated_at,
              b.id,
              b.name,
              b.slug,
              c.id,
              c.name,
              c.slug
            FROM products p
            LEFT JOIN brands b ON p.brand_id = b.id
            LEFT JOIN categories c ON p.category_id = c.id
            ORDER BY p.created_at ASC
            """
        ).fetchall()
        stock_rows = conn.execute(
            """
            SELECT product_id, COALESCE(SUM(quantity - reserved_qty), 0)
            FROM inventory
            GROUP BY product_id
            """
        ).fetchall()
    stock_map = {str(row[0]): int(row[1]) for row in stock_rows}
    actions = []
    for row in product_rows:
        product_id = str(row[0])
        stock = stock_map.get(product_id, 0)
        document = _build_document(row, stock)
        actions.append(
            {
                "_op_type": "index",
                "_index": new_index,
                "_id": product_id,
                "_source": document,
            }
        )
        if len(actions) == 500:
            helpers.bulk(client, actions)
            actions = []
    if actions:
        helpers.bulk(client, actions)
    client.indices.refresh(index=new_index)
    _swap_alias(get_settings().search_index_alias, new_index)
    return new_index
