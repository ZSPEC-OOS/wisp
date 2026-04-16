from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WISP_")

    env: str = "dev"
    db_url: str = "sqlite+aiosqlite:///./wisp.db"
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
    # Optional SearXNG self-hosted search (e.g. "http://localhost:8080")
    searxng_url: str = ""
    # Optional JSON config file for Kubernetes ConfigMap / secret overlay
    config_file: str = ""


def _load_settings() -> Settings:
    s = Settings()
    if s.config_file and Path(s.config_file).exists():
        overrides = json.loads(Path(s.config_file).read_text())
        s = s.model_copy(update=overrides)
    return s


settings = _load_settings()
