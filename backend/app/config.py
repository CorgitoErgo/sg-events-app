"""Application settings, loaded from environment variables and the repo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # blank lines copied from .env.example count as unset
        extra="ignore",
    )

    # Default matches docker-compose.yml's defaults for local development.
    database_url: str = "postgresql+asyncpg://sgevents:sgevents@127.0.0.1:5432/sgevents"

    anthropic_api_key: SecretStr | None = None
    # CLAUDE.md: Haiku for extraction/classification/query parsing (cheap, high-volume).
    anthropic_fast_model: str = "claude-haiku-4-5-20251001"
    voyage_api_key: SecretStr | None = None
    onemap_email: str | None = None
    onemap_password: SecretStr | None = None
    # A token pasted from onemap.gov.sg; lasts 3 days. Email/password renew automatically.
    onemap_token: SecretStr | None = None
    eventbrite_token: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
