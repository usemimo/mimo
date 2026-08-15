"""
tests/conftest.py
-----------------
Shared pytest configuration — loaded before any test module.

Responsibilities
────────────────
1. Inject required environment variables before any app module is imported.
2. Replace the app lifespan with a no-op so TestClient never starts the real
   database, adapter, or background worker.  Each test fixture injects its
   own clean components via app.state.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import patch

# ── 1. Environment variables — must be set BEFORE any app import ──────────────
os.environ.setdefault("WHATSAPP_APP_SECRET", "test_app_secret")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test_verify_token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test_access_token")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("IDEMPOTENCY_DB_PATH", "data/test_idempotency.db")


# ── 2. No-op lifespan — prevents real startup in all tests ───────────────────
@asynccontextmanager
async def _noop_lifespan(app) -> AsyncGenerator[None, None]:
    """
    Replacement lifespan that does nothing at startup/shutdown.
    Tests inject their own db / adapter / idempotency via app.state.
    """
    yield


# Apply the patch before any test module imports create_app().
# The patch replaces `lifespan` in app.main's module namespace so that
# create_app() picks up the no-op when it passes lifespan= to FastAPI().
patch("app.main.lifespan", _noop_lifespan).start()
