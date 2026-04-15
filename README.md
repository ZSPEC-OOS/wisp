# WISP

WISP is a **100% free-by-default**, self-hostable web research platform for agents.
It provides separate surfaces for **search, extract, crawl, map, research** and exposes grounded JSON APIs.

## Features
- DuckDuckGo-first search provider abstraction (pluggable for future providers)
- Resilient extraction with retries, timeout policy, markdown/text output, fallback parsing
- Graph crawl with robots awareness and domain boundary enforcement
- Site mapping from crawl graph with lightweight clustering
- Multi-step research loop with citations, uncertainty notes, and research trace
- Local BM25 passage reranking
- SQLite storage models + in-memory TTL cache strategy
- Prometheus metrics and structured JSON logging
- Benchmark harness with gold fixture tasks

## Quickstart
```bash
cp .env.example .env
pip install -e .[dev]
make run
```
API docs: `http://localhost:8000/docs`

## Make Targets
- `make dev` - run API with reload
- `make run` - run API
- `make test` - run test suite
- `make lint` - run ruff

## Example cURL

### Search
```bash
curl -s localhost:8000/search -X POST -H 'content-type: application/json' -d '{
  "query":"latest LLM retrieval techniques",
  "max_results":5,
  "include_answer":true,
  "include_raw_content":false
}' | jq
```

### Extract
```bash
curl -s localhost:8000/extract -X POST -H 'content-type: application/json' -d '{
  "urls":["https://example.com"],
  "format":"markdown"
}' | jq
```

### Crawl
```bash
curl -s localhost:8000/crawl -X POST -H 'content-type: application/json' -d '{
  "seed_url":"https://example.com",
  "max_pages":10,
  "max_depth":2
}' | jq
```

### Map
```bash
curl -s localhost:8000/map -X POST -H 'content-type: application/json' -d '{
  "seed_url":"https://example.com",
  "max_pages":10,
  "max_depth":2
}' | jq
```

### Research
```bash
curl -s localhost:8000/research -X POST -H 'content-type: application/json' -d '{
  "query":"Compare lexical BM25 and dense retrieval",
  "mode":"report",
  "max_sources":5
}' | jq
```


## API Key Security
WISP now supports optional API-key auth for all core data endpoints (`/search`, `/extract`, `/crawl`, `/map`, `/research`).

- Set `WISP_API_KEYS` to a comma-separated list of accepted keys.
- Send the selected key as `X-API-Key` header.
- `health` and `metrics` remain public for infra compatibility.

Example:
```bash
export WISP_API_KEYS="dev-key-1,dev-key-2"
curl -s localhost:8000/search \
  -X POST \
  -H 'content-type: application/json' \
  -H 'X-API-Key: dev-key-1' \
  -d '{"query":"wisp","max_results":3}'
```

## Tradeoffs
- DDG instant answer endpoint is free and dependable but less complete than commercial indexes.
- Crawl is intentionally polite and constrained; speed is traded for safety and resilience.
- Synthesized answer generation is extractive/grounded by design over fluent abstraction.

## How WISP goes beyond a simple DDG wrapper
- Vertical slice: search -> extract -> passage-rerank -> grounded answer
- First-class crawl, map, and research workflows
- Citation spans, uncertainty notes, and execution trace for inspectability
- Built-in benchmark harness and source-grounding oriented metrics


## Vercel Launch Site
A user-friendly launch site framework is available at `apps/launch-site` with:
- accessible landing page UX
- login/register framework
- Firestore config placeholders
- subscription usage-credit framework with admin override

Run locally:
```bash
cd apps/launch-site
python -m http.server 4173
```
