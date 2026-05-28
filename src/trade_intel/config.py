"""Application configuration.

All settings are env-driven via `pydantic-settings`. A `.env` at the project
root is loaded automatically; real environment variables always take precedence.

Usage:
    from trade_intel.config import get_settings
    settings = get_settings()
    engine = create_engine(settings.database_url)
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class VectorStoreType(StrEnum):
    FAISS = "faiss"
    QDRANT = "qdrant"


_DEFAULT_SQLITE_PATH = (PROJECT_ROOT / "data" / "dev.db").as_posix()


class Settings(BaseSettings):
    """Typed, validated runtime configuration.

    Override any field via environment variable (case-insensitive) or `.env`.
    Unknown env vars are ignored so Phase 5+ additions (worker queue, API key)
    can coexist without bumping this module.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # App
    app_name: str = "trade-intel"
    environment: Environment = Environment.DEV
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO

    # Database
    sqlite_url: str = f"sqlite:///{_DEFAULT_SQLITE_PATH}"
    postgres_url: str | None = None
    db_echo: bool = False

    # ML / Vector
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    vector_store_type: VectorStoreType = VectorStoreType.FAISS
    faiss_index_dir: Path = PROJECT_ROOT / "data" / "vectors"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_reload: bool = False
    api_key: str | None = Field(
        default=None,
        description=(
            "Shared secret for the X-API-Key header (Phase 6 auth). "
            "When None / empty, the API runs in dev-open mode and accepts "
            "all requests."
        ),
    )

    @field_validator("sqlite_url")
    @classmethod
    def _validate_sqlite_url(cls, v: str) -> str:
        if not v.startswith("sqlite:"):
            raise ValueError("sqlite_url must use the sqlite:// scheme")
        return v

    @field_validator("postgres_url")
    @classmethod
    def _validate_postgres_url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not (v.startswith("postgresql://") or v.startswith("postgresql+")):
            raise ValueError(
                "postgres_url must start with postgresql:// or postgresql+<driver>://"
            )
        return v

    @model_validator(mode="after")
    def _enforce_prod_safety(self) -> Settings:
        if self.environment is Environment.PROD:
            if not self.postgres_url:
                raise ValueError("postgres_url is required when environment=prod")
            if self.debug:
                raise ValueError("debug must be False in prod")
            if self.api_reload:
                raise ValueError("api_reload must be False in prod")
            if not self.api_key:
                raise ValueError("api_key is required when environment=prod")
        return self

    @property
    def database_url(self) -> str:
        """Active DB URL: Postgres in prod when configured, otherwise SQLite."""
        if self.environment is Environment.PROD and self.postgres_url:
            return self.postgres_url
        return self.sqlite_url

    @property
    def is_prod(self) -> bool:
        return self.environment is Environment.PROD

    @property
    def is_dev(self) -> bool:
        return self.environment is Environment.DEV

    @property
    def is_test(self) -> bool:
        return self.environment is Environment.TEST


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Call `get_settings.cache_clear()` in tests."""
    return Settings()
