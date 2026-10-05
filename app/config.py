"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (and .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # GitHub App
    github_app_id: int = 0
    github_private_key_path: str = "private-key.pem"
    github_webhook_secret: str = ""

    # LLM
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # Pipeline
    max_attempts: int = 3
    sandbox_timeout: int = 180
    database_url: str = "sqlite:///fixci.db"

    # Sandbox escape hatch for tests / local dry runs.
    sandbox_enabled: bool = True

    @property
    def sqlite_path(self) -> str:
        """Return the filesystem path for the SQLite database."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///") :]
        if url.startswith("sqlite://"):
            return url[len("sqlite://") :]
        return url

    def load_private_key(self) -> str:
        """Read the GitHub App private key PEM from disk."""
        with open(self.github_private_key_path, "r", encoding="utf-8") as fh:
            return fh.read()


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()
