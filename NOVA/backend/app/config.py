"""Application settings (12-factor style, env driven).

All variables use the ``NOVA_`` prefix and can also live in a ``.env`` file.
Import :func:`get_settings` anywhere; it is cached so the environment is read
once per process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOVA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "NOVA – Beyond the Mission API"
    environment: Literal["dev", "test", "prod"] = "dev"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Persistence -------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/nova.db"
    db_echo: bool = False
    auto_create_tables: bool = True
    db_pool_size: int = 5

    # --- HTTP --------------------------------------------------------------
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_origin_regex: str | None = r"https://.*\.(e2b\.app|vercel\.app|netlify\.app)"
    max_request_bytes: int = 256_000

    # --- Security ----------------------------------------------------------
    auth_enabled: bool = False
    secret_key: str = "nova-dev-secret-change-me"
    token_ttl_hours: int = 72
    api_key: str = ""

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = True
    # A legitimate client sends ~10 simulation ticks/s + 1 life-support tick/s
    # + interactions, so the ceiling must stay above ~700 requests/minute.
    rate_limit_requests: int = 1800
    rate_limit_window_seconds: int = 60

    # --- Gameplay ----------------------------------------------------------
    default_difficulty: Literal["cadet", "standard", "veteran"] = "standard"
    max_tick_seconds: float = Field(default=5.0, gt=0, le=60)
    session_ttl_hours: int = 168
    max_events_per_session: int = 2000
    max_sessions: int = 5000
    content_dir: Path = PROJECT_ROOT / "content"
    #: Run the static mission-content validator at startup.
    validate_content: bool = True
    #: Refuse to boot when the content pack contains errors (unwinnable missions).
    validate_content_strict: bool = True

    # --- Live event stream (WebSocket) -------------------------------------
    # The stream is a read model over the persisted event log: the bus is a
    # doorbell, the log is the truth. These knobs trade latency for load.
    #: Idle keep-alive interval, also announced to the HUD in ``stream.ready``.
    stream_heartbeat_seconds: float = Field(default=15.0, gt=0, le=300)
    #: Safety net for multi-worker deployments (the bus is process-local):
    #: re-read the log this often even when no notification arrived.
    stream_reconcile_seconds: float = Field(default=5.0, gt=0, le=600)
    #: Events per backfill page (initial replay, resume and re-sync).
    stream_batch_size: int = Field(default=200, ge=1, le=2000)
    #: Buffered frames per socket. A slow consumer overflows, gets
    #: ``stream.resync`` and is refilled from its cursor – nothing is lost.
    stream_queue_size: int = Field(default=256, ge=1, le=10_000)
    #: Sockets one save may hold open (HUD + mission panel + spectators).
    stream_max_connections_per_game: int = Field(default=8, ge=1, le=64)

    # --- NASA open data ----------------------------------------------------
    nasa_api_key: str = "DEMO_KEY"
    nasa_enabled: bool = True
    nasa_base_url: str = "https://api.nasa.gov"
    nasa_timeout_seconds: float = 8.0
    nasa_cache_ttl_seconds: int = 900
    nasa_max_retries: int = 2

    @field_validator("log_level")
    @classmethod
    def _normalise_level(cls, value: str) -> str:
        return value.upper()

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that patch the environment between app builds."""
    get_settings.cache_clear()
