"""Application settings, loaded from environment variables only (never from code)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration. See .env.example for the full list."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "prod"] = "dev"
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    preload_detector: bool = Field(
        default=True,
        description="Load the AI-text detection model at startup instead of first request.",
    )

    # Secrets. Empty until the task that needs them; the app must start without them.
    anthropic_api_key: str = ""
    hf_api_token: str = ""

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string so .env stays readable."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
