from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

from packages.crawl.service import CrawlService


class MapService:
    def __init__(self, crawler: CrawlService):
        self.crawler = crawler

    async def build_map(self, seed_url: str, max_pages: int = 20, max_depth: int = 2) -> dict:
        data = await self.crawler.crawl(seed_url=seed_url, max_pages=max_pages, max_depth=max_depth)
        clusters = defaultdict(list)
        for n in data["nodes"]:
            parsed = urlparse(n["url"])
            parts = [p for p in parsed.path.split("/") if p]
            path_bucket = parts[0] if parts else "root"
            clusters[path_bucket].append(n["url"])
        return {
            "nodes": data["nodes"],
            "edges": data["edges"],
            "clusters": dict(clusters),
            "site_summary": {
                "total_nodes": len(data["nodes"]),
                "total_edges": len(data["edges"]),
                "failure_count": len(data["failures"]),
            },
        }
