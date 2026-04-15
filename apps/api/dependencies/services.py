from packages.crawl.service import CrawlService
from packages.extract.service import ExtractService
from packages.map.service import MapService
from packages.research.service import ResearchService
from packages.search.pipeline import SearchService
from packages.storage.cache import TTLCache

from apps.api.config import settings

extract_service = ExtractService(user_agent=settings.user_agent, timeout_seconds=settings.http_timeout)
search_service = SearchService()
crawl_service = CrawlService(extractor=extract_service)
map_service = MapService(crawler=crawl_service)
research_service = ResearchService(search=search_service, extract=extract_service)
cache = TTLCache(ttl_seconds=settings.cache_ttl_seconds)
