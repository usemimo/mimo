"""
app/core/config.py
------------------
Centralised settings loaded from environment variables.
Uses pydantic-settings so every variable is type-validated at startup.
No secret defaults — the app will refuse to start if required vars are missing.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration is read from environment variables (or a .env file in
    development).  Never add real secret values here — only env var names.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently ignore unknown env vars
    )

    # ── WhatsApp Cloud API ────────────────────────────────────────────────────
    whatsapp_app_secret: str        # used for HMAC-SHA256 signature verification
    whatsapp_verify_token: str      # token you set in the Meta developer portal
    whatsapp_phone_number_id: str   # your WA Business phone number ID
    whatsapp_access_token: str      # long-lived / temporary API access token

    # ── AI Orchestration (Phase 6+) ───────────────────────────────────────────
    openai_api_key: str | None = None  # API key for OpenAI model

    # ── App ──────────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    app_env: str = "development"

    # ── Database (Phase 3 — Postgres) ────────────────────────────────────────
    postgres_dsn: str = "postgresql://user:password@localhost:5432/mimo"
    """
    DSN for the PostgreSQL database (e.g., postgresql://user:pass@host:5432/db).
    """


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached singleton Settings instance.

    Use this everywhere instead of constructing Settings() directly so that
    the .env file is only parsed once per process.
    """
    return Settings()
