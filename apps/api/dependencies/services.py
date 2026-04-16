from apps.api.config import settings
from packages.crawl.service import CrawlService
from packages.extract.service import ExtractService
from packages.map.service import MapService
from packages.research.llm import LlmSynthesisClient
from packages.research.service import ResearchService
from packages.search.academic_providers import ArxivProvider, OpenAlexProvider, SemanticScholarProvider
from packages.search.enrichers import UnpaywallResolver
from packages.search.pipeline import SearchService
from packages.search.providers import DuckDuckGoProvider, SearXNGProvider
from packages.storage.cache import TTLCache

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

# Use SearXNG when configured, otherwise fall back to DuckDuckGo
_web_provider = (
    SearXNGProvider(base_url=settings.searxng_url)
    if settings.searxng_url
    else DuckDuckGoProvider()
)

search_service = SearchService(provider=_web_provider, academic_providers=_academic_providers)

# Unpaywall resolver — only active when an email is configured (required by ToS)
_unpaywall = UnpaywallResolver(email=settings.academic_mailto) if settings.academic_mailto else None

crawl_service = CrawlService(extractor=extract_service)
map_service   = MapService(crawler=crawl_service)

# LLM synthesis client — only instantiated when the feature is enabled so the
# httpx connection pool is not opened for deployments that never use synthesis.
_llm_client = LlmSynthesisClient() if settings.llm_enabled else None

research_service = ResearchService(
    search=search_service,
    extract=extract_service,
    unpaywall=_unpaywall,
    llm=_llm_client,
)
cache = TTLCache(ttl_seconds=settings.cache_ttl_seconds)
