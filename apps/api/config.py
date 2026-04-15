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


settings = Settings()
