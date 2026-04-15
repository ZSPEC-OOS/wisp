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

## Tradeoffs
- DDG instant answer endpoint is free and dependable but less complete than commercial indexes.
- Crawl is intentionally polite and constrained; speed is traded for safety and resilience.
- Synthesized answer generation is extractive/grounded by design over fluent abstraction.

## How WISP goes beyond a simple DDG wrapper
- Vertical slice: search -> extract -> passage-rerank -> grounded answer
- First-class crawl, map, and research workflows
- Citation spans, uncertainty notes, and execution trace for inspectability
- Built-in benchmark harness and source-grounding oriented metrics
