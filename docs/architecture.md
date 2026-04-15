# WISP Architecture

```mermaid
flowchart TD
    A[Client/Agent] --> B[FastAPI API]
    B --> C[Search Service]
    B --> D[Extract Service]
    B --> E[Crawl Service]
    B --> F[Map Service]
    B --> G[Research Service]
    C --> H[DuckDuckGo Provider]
    D --> I[HTTP Fetch + Trafilatura]
    E --> D
    F --> E
    G --> C
    G --> D
    G --> J[Ranking BM25]
    B --> K[TTL Cache]
    B --> L[Prometheus Metrics]
    B --> M[SQLite/SQLAlchemy]
```

## TODO (Future Enhancements)
- Optional local embeddings reranker toggle with sentence-transformers.
- Pluggable providers for SearXNG and Brave-compatible local gateways.
- Async queue-backed crawl workers and checkpoint resume.
- Richer contradiction detection and claim-level provenance alignment.
