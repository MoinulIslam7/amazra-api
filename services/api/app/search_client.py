from elasticsearch import Elasticsearch

from .config import get_settings

_client: Elasticsearch | None = None


def init_search() -> None:
    """Initialize the shared Elasticsearch client."""
    global _client
    settings = get_settings()
    if not settings.elasticsearch_url:
        raise RuntimeError("ELASTICSEARCH_URL is required")
    _client = Elasticsearch(settings.elasticsearch_url)


def get_search_client() -> Elasticsearch:
    """Return the shared Elasticsearch client."""
    if not _client:
        raise RuntimeError("Search client not initialized")
    return _client
