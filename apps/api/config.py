from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WISP_")

    config_file: str = ""  # path to a JSON file whose keys override env vars
    env: str = "dev"
    db_url: str = "sqlite+aiosqlite:///./wisp.db"
    redis_url: str = ""            # e.g. "redis://localhost:6379" — enables distributed cache + rate limiting
    cache_key_prefix: str = "wisp:cache:"
    http_timeout: int = 12
    user_agent: str = "WISPBot/0.1 (+https://localhost)"
    cache_ttl_seconds: int = 900
    enable_embeddings: bool = False
    log_level: str = "INFO"
    api_keys: str = ""
    rate_limit_per_minute: int = 0  # 0 = disabled; set to e.g. 60 to enforce per-key limits
    research_rate_limit_per_minute: int = 0  # override for /research (0 = use global)
    crawl_rate_limit_per_minute: int = 0     # override for /crawl (0 = use global)
    # Academic open-access pipeline
    academic_mailto: str = ""        # Polite-pool email for OpenAlex + CrossRef + Unpaywall
    s2_api_key: str = ""             # Optional Semantic Scholar key (higher rate limits)
    academic_max_results: int = 4    # Results per academic provider
    searxng_url: str = ""            # e.g. "http://localhost:8080" — self-hosted SearXNG instance
    brave_api_key: str = ""          # Brave Search API key — enables high-quality direct web search

    # Per-phase timeouts for the research pipeline
    search_timeout_seconds:  float = 15.0   # per search call inside /research
    extract_timeout_seconds: float = 30.0   # total extraction budget inside /research

    # ── Optional LLM synthesis (Qwen3-8B or any OpenAI-compatible endpoint) ──
    # Global off switch — all LLM paths are no-ops when False
    llm_enabled: bool = False

    # Inference endpoint
    llm_base_url: str  = "http://localhost:8001/v1"
    llm_api_key:  str  = ""       # must be set when llm_enabled=True
    llm_model:    str  = "Qwen/Qwen3-8B"

    # Latency budgets — per-mode overrides take precedence over llm_timeout_seconds.
    # Defaults are conservative; tighten after measuring p95 on target hardware.
    llm_timeout_seconds:            float = 8.0
    llm_timeout_concise_seconds:    float = 5.0
    llm_timeout_report_seconds:     float = 12.0
    llm_timeout_structured_seconds: float = 10.0

    # Scope and quality
    llm_max_context_evidence: int   = 6
    llm_temperature:          float = 0.2
    llm_max_tokens:           int   = 900

    # Thinking mode: off by default for bounded synthesis (reduces latency
    # and JSON instability against the hard timeout budget)
    llm_enable_thinking: bool = False

    # Default gate mode: auto | never | always
    llm_synthesis_mode_default: str = "auto"

    # Gate thresholds — exposed as config for empirical tuning; never hardcode
    llm_gate_clear_winner_margin:        float = 0.12
    llm_gate_clear_winner_ratio:         float = 1.25
    llm_gate_min_confidence:             float = 0.40
    llm_gate_report_min_confidence:      float = 0.45
    llm_gate_synthesis_intent_threshold: float = 0.10


def _load_settings() -> Settings:
    settings_obj = Settings()
    if settings_obj.config_file and Path(settings_obj.config_file).exists():
        overrides = json.loads(Path(settings_obj.config_file).read_text())
        settings_obj = settings_obj.model_copy(update=overrides)
    return settings_obj


settings = _load_settings()
