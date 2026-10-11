"""Settings for the product API, read from ``PRODUCT_API_*`` environment variables."""

import logging
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. The database URL has no default so no credentials live in code."""

    model_config = SettingsConfigDict(env_prefix="PRODUCT_API_", env_file=".env")

    database_url: str
    log_level: str = "INFO"
    max_upload_bytes: int = 1_048_576
    oidc_audience: str = "e2e-self-heal-product"
    oidc_jwks_ttl_seconds: int = 300
    allowed_repository_owners: list[str] = []

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in logging.getLevelNamesMapping():
            names = ", ".join(sorted(logging.getLevelNamesMapping()))
            raise ValueError(f"unknown log level {value!r}; expected one of {names}")
        return level

    @field_validator("max_upload_bytes")
    @classmethod
    def _positive_upload_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_upload_bytes must be positive")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
