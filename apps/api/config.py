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
    # Academic open-access pipeline
    academic_mailto: str = ""        # Polite-pool email for OpenAlex + CrossRef + Unpaywall
    s2_api_key: str = ""             # Optional Semantic Scholar key (higher rate limits)
    academic_max_results: int = 4    # Results per academic provider
    searxng_url: str = ""            # e.g. "http://localhost:8080" — self-hosted SearXNG instance


settings = Settings()
