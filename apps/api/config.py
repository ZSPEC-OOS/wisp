import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WISP_")

    config_file: str = ""  # path to a JSON file whose keys override env vars
    env: str = "dev"
    db_url: str = "sqlite+aiosqlite:///./wisp.db"
    http_timeout: int = 12
    user_agent: str = "WISPBot/0.1 (+https://localhost)"
    cache_ttl_seconds: int = 900
    enable_embeddings: bool = False
    log_level: str = "INFO"
    api_keys: str = ""
    rate_limit_per_minute: int = 0  # 0 = disabled; set to e.g. 60 to enforce per-key limits
    # Academic open-access pipeline
    academic_mailto: str = ""        # Polite-pool email for OpenAlex + CrossRef + Unpaywall
    s2_api_key: str = ""             # Optional Semantic Scholar key (higher rate limits)
    academic_max_results: int = 4    # Results per academic provider
    searxng_url: str = ""            # e.g. "http://localhost:8080" — self-hosted SearXNG instance


def _load_settings() -> Settings:
    settings_obj = Settings()
    if settings_obj.config_file and Path(settings_obj.config_file).exists():
        overrides = json.loads(Path(settings_obj.config_file).read_text())
        settings_obj = settings_obj.model_copy(update=overrides)
    return settings_obj


settings = _load_settings()
