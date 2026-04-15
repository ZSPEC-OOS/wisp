from __future__ import annotations

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
    ) -> dict:
        seed = canonicalize_url(seed_url)
        base_domain = domain_of(seed)
        allow = set(allowed_domains or [base_domain])
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(seed, "/robots.txt"))
        try:
            rp.read()
        except Exception:
            pass

        visited = set()
        q = deque([(seed, 0)])
        edges, nodes, failures = [], [], []

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            # Seed additional URLs from sitemap.xml
            sitemap_urls: list[str] = list(rp.site_maps() or [])
            if not sitemap_urls:
                sitemap_urls = [urljoin(seed, "/sitemap.xml")]
            for sitemap_url in sitemap_urls:
                try:
                    resp = await client.get(sitemap_url)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "lxml-xml")
                        for loc in soup.find_all("loc"):
                            loc_url = canonicalize_url(loc.text.strip())
                            if loc_url and domain_of(loc_url) in allow and loc_url not in visited:
                                q.append((loc_url, 0))
                except Exception:
                    pass

            while q and len(visited) < max_pages:
                url, depth = q.popleft()
                if url in visited or depth > max_depth:
                    continue
                if domain_of(url) not in allow:
                    continue
                if not rp.can_fetch(self.extractor.user_agent, url):
                    failures.append({"url": url, "error": "blocked_by_robots"})
                    continue
                visited.add(url)
                try:
                    r = await client.get(url, headers={"User-Agent": self.extractor.user_agent})
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
                except Exception as exc:
                    failures.append({"url": url, "error": str(exc)})
        return {
            "pages_crawled": len(nodes),
            "nodes": nodes,
            "edges": edges,
            "discovered_urls": sorted({e["to"] for e in edges}),
            "failures": failures,
        }
