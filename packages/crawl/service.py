from __future__ import annotations

import asyncio
from collections import deque
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from packages.common.url import canonicalize_url, domain_of
from packages.extract.service import ExtractService


class CrawlService:
    def __init__(self, extractor: ExtractService):
        self.extractor = extractor

    async def crawl(
        self,
        seed_url: str,
        max_pages: int = 10,
        max_depth: int = 2,
        allowed_domains: list[str] | None = None,
        timeout_seconds: int = 10,
        concurrency: int = 5,
    ) -> dict:
        seed = canonicalize_url(seed_url)
        base_domain = domain_of(seed)
        allow = set(allowed_domains or [base_domain])
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(seed, "/robots.txt"))
        crawl_delay = 0.0
        try:
            rp.read()
            crawl_delay = rp.crawl_delay(self.extractor.user_agent) or 0.0
        except Exception:
            pass

        visited: set[str] = set()
        q: deque[tuple[str, int]] = deque([(seed, 0)])
        edges: list[dict] = []
        nodes: list[dict] = []
        failures: list[dict] = []

        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            # Seed additional URLs from sitemap.xml
            sitemap_urls: list[str] = list(rp.site_maps() or [])
            if not sitemap_urls:
                sitemap_urls = [urljoin(seed, "/sitemap.xml")]
            for sitemap_url in sitemap_urls:
                try:
                    resp = await client.get(sitemap_url)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "lxml-xml")
                        if soup.find("sitemapindex"):
                            # Recurse one level into child sitemaps
                            child_sitemap_locs = [loc.text.strip() for loc in soup.find_all("loc")]
                            
                            async def _fetch_child_sitemap(child_url: str) -> list[str]:
                                try:
                                    child_resp = await client.get(child_url)
                                    if child_resp.status_code == 200:
                                        child_soup = BeautifulSoup(child_resp.text, "lxml-xml")
                                        return [
                                            canonicalize_url(loc.text.strip()) for loc in child_soup.find_all("loc")
                                        ]
                                except Exception:
                                    pass
                                return []

                            child_results = await asyncio.gather(
                                *[_fetch_child_sitemap(u) for u in child_sitemap_locs[:5]]
                            )
                            for urls in child_results:
                                for loc_url in urls:
                                    if loc_url and domain_of(loc_url) in allow and loc_url not in visited:
                                        q.append((loc_url, 0))
                        else:
                            for loc in soup.find_all("loc"):
                                loc_url = canonicalize_url(loc.text.strip())
                                if loc_url and domain_of(loc_url) in allow and loc_url not in visited:
                                    q.append((loc_url, 0))
                except Exception:
                    pass

            sem = asyncio.Semaphore(concurrency)

            async def _fetch_page(url: str, depth: int) -> None:
                if url in visited or len(visited) >= max_pages:
                    return
                if depth > max_depth or domain_of(url) not in allow:
                    return
                if not rp.can_fetch(self.extractor.user_agent, url):
                    failures.append({"url": url, "error": "blocked_by_robots"})
                    return
                visited.add(url)
                async with sem:
                    try:
                        r = await client.get(url, headers={"User-Agent": self.extractor.user_agent})
                        ct = r.headers.get("content-type", "")
                        if not any(t in ct for t in ("text/html", "application/xhtml")):
                            # Not HTML — record as skipped, don't parse links
                            nodes.append({"url": url, "title": None, "depth": depth, "skipped": True})
                            return
                        soup = BeautifulSoup(r.text, "lxml")
                        title = (soup.title.text or "").strip() if soup.title else None
                        nodes.append({"url": url, "title": title, "depth": depth})
                        for a in soup.select("a[href]"):
                            nxt = canonicalize_url(urljoin(url, a.get("href")))
                            if urlparse(nxt).scheme not in {"http", "https"}:
                                continue
                            edges.append({"from": url, "to": nxt})
                            if nxt not in visited and domain_of(nxt) in allow:
                                q.append((nxt, depth + 1))
                        if crawl_delay > 0:
                            await asyncio.sleep(crawl_delay)
                    except Exception as exc:
                        failures.append({"url": url, "error": str(exc)})

            # Process in batches of crawl concurrency
            while q and len(visited) < max_pages:
                batch = []
                while q and len(batch) < concurrency:
                    batch.append(q.popleft())
                await asyncio.gather(*[_fetch_page(url, depth) for url, depth in batch])

        return {
            "pages_crawled": len(nodes),
            "nodes": nodes,
            "edges": edges,
            "discovered_urls": sorted({e["to"] for e in edges}),
            "failures": failures,
        }
