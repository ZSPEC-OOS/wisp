import pytest

from packages.crawl.service import CrawlService
from packages.extract.service import ExtractService


@pytest.mark.asyncio
async def test_crawl_enforces_domain_boundary():
    svc = CrawlService(ExtractService(user_agent="wisp-test"))
    data = await svc.crawl("https://example.com", max_pages=2, max_depth=1, allowed_domains=["example.com"])
    assert all("example.com" in n["url"] for n in data["nodes"])
