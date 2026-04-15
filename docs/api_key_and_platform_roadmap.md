# WISP Enhancements Roadmap (API-Key and Agent Tooling)

This roadmap focuses on evolving WISP into a production-friendly web search/crawl tool that can be safely consumed by an AI coding agent.

## 1) API Key and Access Model

### Implemented baseline
- Static API key enforcement via `X-API-Key` with `WISP_API_KEYS`.
- Public observability endpoints (`/health`, `/metrics`) to keep deployment probes simple.

### Next upgrades
1. **Hashed key storage**
   - Store key identifiers + salted hash (never raw keys in settings files).
   - Add one-time key reveal at creation.
2. **Scopes and permissions**
   - Example scopes: `search:read`, `crawl:run`, `research:run`, `admin:keys`.
   - Enforce per-route scope checks.
3. **Rotation and revocation**
   - Expirable keys and scheduled rotation windows.
   - Immediate revoke endpoint + audit event.
4. **Per-key usage limits**
   - Rate limits (req/min) and monthly quotas keyed by API key ID.

## 2) Multi-Tenant Readiness

1. **Tenant boundary in storage**
   - Add `tenant_id` on cached artifacts, jobs, and crawl graphs.
2. **Tenant-level config policies**
   - Allowed/blocked domains, crawl depth caps, extraction limits.
3. **Isolation controls**
   - Avoid cross-tenant cache hits by namespacing cache keys.

## 3) Reliability for Long Crawls

1. **Async job queue**
   - Convert crawl/map/research into async jobs (enqueue + poll + cancel).
2. **Checkpointing**
   - Persist crawl frontier state for resume-on-restart.
3. **Backpressure**
   - Global and per-tenant concurrency limits to prevent resource starvation.

## 4) Search Quality and Provider Flexibility

1. **Provider adapters**
   - Add Brave/SearXNG providers with weighted failover strategy.
2. **Source credibility controls**
   - Domain reputation policy and optional trust allowlists.
3. **Reranking upgrades**
   - Hybrid lexical+dense reranker toggle for better technical relevance.

## 5) Agent Integration UX

1. **Agent-native endpoint**
   - A single `/agent/query` endpoint returning compact reasoning-ready chunks.
2. **Structured grounding contracts**
   - Every answer chunk should include URL, extract timestamp, and confidence.
3. **Tool profile presets**
   - Profiles such as `fast_search`, `deep_research`, `safe_crawl`.

## 6) Security and Governance

1. **SSRF hardening**
   - Block local/private networks by default.
2. **Abuse monitoring**
   - Alert on unusual key behavior (bursting, forbidden target patterns).
3. **Audit trails**
   - Persist key usage logs with endpoint, status, and request IDs.

## 7) Commercialization Readiness

1. **Billing metrics**
   - Meter billable units: search calls, extracted pages, crawl pages, job runtime.
2. **Plan entitlements**
   - Feature flags by plan tier (max pages, enabled providers, retention).
3. **Admin API/UI**
   - Self-serve key issuance, rotation, and usage dashboards.

## Suggested implementation order
1. Hashed API keys + scopes
2. Per-key rate limiting + usage logs
3. Async crawl/research jobs with checkpointing
4. Provider adapters + hybrid reranking
5. Tenant model + billing/plan enforcement
