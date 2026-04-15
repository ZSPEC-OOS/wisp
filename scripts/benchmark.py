from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

DATASET = Path("tests/fixtures/research_gold.json")


async def run():
    items = json.loads(DATASET.read_text())
    latencies = []
    grounded = 0
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=60) as client:
        for item in items:
            start = time.perf_counter()
            resp = await client.post("/research", json={"query": item["query"], "mode": "concise"})
            latencies.append(time.perf_counter() - start)
            payload = resp.json()
            if payload.get("sources"):
                grounded += 1
    print(
        json.dumps(
            {
                "tasks": len(items),
                "citation_coverage": grounded / len(items),
                "answer_grounding_rate": grounded / len(items),
                "median_latency_seconds": statistics.median(latencies) if latencies else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
