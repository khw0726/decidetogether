from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    compiler_model: str = "claude-sonnet-4-6"
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"
    escalation_confidence_threshold: float = 0.75
    database_url: str = "sqlite+aiosqlite:///./automod.db"


settings = Settings()
