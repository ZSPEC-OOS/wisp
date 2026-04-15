from __future__ import annotations

from collections import defaultdict

from packages.crawl.service import CrawlService


class MapService:
    def __init__(self, crawler: CrawlService):
        self.crawler = crawler

    async def build_map(self, seed_url: str, max_pages: int = 20, max_depth: int = 2) -> dict:
        data = await self.crawler.crawl(seed_url=seed_url, max_pages=max_pages, max_depth=max_depth)
        clusters = defaultdict(list)
        for n in data["nodes"]:
            path_bucket = n["url"].split("/")[3] if len(n["url"].split("/")) > 3 else "root"
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
