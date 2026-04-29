import anthropic
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_access_key: str = ""
    aws_secret_key: str = ""
    aws_region: str = "ap-northeast-2"
    compiler_model: str = "global.anthropic.claude-sonnet-4-6"
    sonnet_model: str = "global.anthropic.claude-sonnet-4-6"
    haiku_model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    escalation_confidence_threshold: float = 0.75
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    embedding_dim: int = 1024
    database_url: str = "sqlite+aiosqlite:///./automod.db"

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""
    reddit_password: str = ""
    reddit_user_agent: str = "automod-agent/2.0"


settings = Settings()


def get_anthropic_client() -> anthropic.AsyncAnthropicBedrock:
    return anthropic.AsyncAnthropicBedrock(
        aws_access_key=settings.aws_access_key,
        aws_secret_key=settings.aws_secret_key,
        aws_region=settings.aws_region,
    )
