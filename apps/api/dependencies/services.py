from apps.api.config import settings
from packages.crawl.service import CrawlService
from packages.extract.service import ExtractService
from packages.map.service import MapService
from packages.research.service import ResearchService
from packages.search.academic_providers import ArxivProvider, OpenAlexProvider, SemanticScholarProvider
from packages.search.enrichers import UnpaywallResolver
from packages.search.pipeline import SearchService
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

search_service = SearchService(academic_providers=_academic_providers)

# Unpaywall resolver — only active when an email is configured (required by ToS)
_unpaywall = UnpaywallResolver(email=settings.academic_mailto) if settings.academic_mailto else None

crawl_service = CrawlService(extractor=extract_service)
map_service = MapService(crawler=crawl_service)
research_service = ResearchService(search=search_service, extract=extract_service, unpaywall=_unpaywall)
cache = TTLCache(ttl_seconds=settings.cache_ttl_seconds)
