from apps.api.config import settings
from packages.crawl.service import CrawlService
from packages.extract.service import ExtractService
from packages.map.service import MapService
from packages.research.llm import LlmSynthesisClient
from packages.research.service import ResearchService
from packages.search.academic_providers import ArxivProvider, OpenAlexProvider, SemanticScholarProvider
from packages.search.enrichers import CrossRefEnricher, UnpaywallResolver
from packages.search.pipeline import SearchService
from packages.search.providers import BraveSearchProvider, DuckDuckGoProvider, SearXNGProvider
from packages.storage.cache import RedisTTLCache, TTLCache

extract_service = ExtractService(user_agent=settings.user_agent, timeout_seconds=settings.http_timeout)

# Academic providers — OpenAlex and arXiv are always on (no key needed).
# Semantic Scholar is added when a key is configured (or always, at lower rate).
_academic_providers = [
    OpenAlexProvider(mailto=settings.academic_mailto, per_page=settings.academic_max_results),
    ArxivProvider(max_results=settings.academic_max_results),
    SemanticScholarProvider(
        api_key=settings.s2_api_key,
        limit=settings.academic_max_results,
    ),
]

# Web search provider priority:
#   1. Brave (if WISP_BRAVE_API_KEY set) — direct REST API, highest quality
#   2. SearXNG (if WISP_SEARXNG_URL set) — self-hosted aggregator
#   3. DuckDuckGo — free fallback (scraping-based)
# DDG is always kept as a fallback provider so fan-out still works when a
# premium provider is the primary.
_ddg = DuckDuckGoProvider()
if settings.brave_api_key:
    _web_provider = BraveSearchProvider(api_key=settings.brave_api_key)
    _web_fallbacks = [SearXNGProvider(base_url=settings.searxng_url) if settings.searxng_url else _ddg]
elif settings.searxng_url:
    _web_provider = SearXNGProvider(base_url=settings.searxng_url)
    _web_fallbacks = [_ddg]
else:
    _web_provider = _ddg
    _web_fallbacks = []

search_service = SearchService(
    provider=_web_provider,
    academic_providers=_academic_providers,
    fallback_providers=_web_fallbacks,
)

# Academic enrichers — both require mailto for polite-pool access
_unpaywall = UnpaywallResolver(email=settings.academic_mailto) if settings.academic_mailto else None
_crossref  = CrossRefEnricher(mailto=settings.academic_mailto) if settings.academic_mailto else None

crawl_service = CrawlService(extractor=extract_service)
map_service   = MapService(crawler=crawl_service)

# LLM synthesis client — only instantiated when the feature is enabled so the
# httpx connection pool is not opened for deployments that never use synthesis.
_llm_client = LlmSynthesisClient() if settings.llm_enabled else None

research_service = ResearchService(
    search=search_service,
    extract=extract_service,
    unpaywall=_unpaywall,
    crossref=_crossref,
    llm=_llm_client,
)

# Cache: Redis-backed when WISP_REDIS_URL is set (shared across workers),
# otherwise in-process TTLCache (fast, no external dependency).
cache: RedisTTLCache | TTLCache = (
    RedisTTLCache(
        redis_url=settings.redis_url,
        ttl_seconds=settings.cache_ttl_seconds,
        key_prefix=settings.cache_key_prefix,
    )
    if settings.redis_url
    else TTLCache(ttl_seconds=settings.cache_ttl_seconds)
)
