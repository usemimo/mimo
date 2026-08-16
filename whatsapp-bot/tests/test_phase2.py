"""
tests/test_phase2.py
--------------------
Phase 2 acceptance criteria tests.

AC-1: Bot receives a message and sends a reply (text or buttons) end-to-end
AC-2: Resending the same webhook event does NOT produce a duplicate reply
AC-3: /health returns a meaningful status
AC-4: Help command → buttons, fallback for unknown text, fallback for non-text

Fix notes vs first attempt
────────────────────────────
• Each helper call generates a unique wamid so tests don't bleed idempotency
  state into one another.
• app_with_mocks uses lifespan=False and sets app.state directly — avoids
  the lifespan spinning up a second DB instance that shadows our mock.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# Env vars and lifespan patch are applied in conftest.py before this module loads
from app.adapters.whatsapp import WhatsAppAdapter
from app.core.idempotency import IdempotencyStore
from app.db.sqlite import SQLiteDatabase
from app.main import create_app

APP_SECRET = "test_app_secret"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    mac = hmac.new(key=secret.encode(), msg=body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _unique_wamid() -> str:
    """Generate a fresh wamid per call so no two test calls share state."""
    return f"wamid.{uuid.uuid4().hex[:12]}"


def _wa_payload(
    wamid: str | None = None,
    from_id: str = "15550001234",
    text: str = "Hello",
    msg_type: str = "text",
) -> dict:
    """Build a minimal but valid WhatsApp Cloud API webhook payload."""
    wamid = wamid or _unique_wamid()
    msg: dict[str, Any] = {
        "id": wamid,
        "from": from_id,
        "timestamp": "1700000000",
        "type": msg_type,
    }
    if msg_type == "text":
        msg["text"] = {"body": text}

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "e1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {},
            "contacts": [{"profile": {"name": "Test"}, "wa_id": from_id}],
            "messages": [msg],
        }}]}],
    }


def _post_webhook(client: TestClient, payload: dict, secret: str = APP_SECRET):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, secret),
        },
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_adapter():
    """WhatsApp adapter with all send methods mocked — zero network calls."""
    adapter = MagicMock(spec=WhatsAppAdapter)
    adapter.send_text = AsyncMock(return_value=True)
    adapter.send_buttons = AsyncMock(return_value=True)
    adapter.send_template = AsyncMock(return_value=True)
    adapter.mark_read = AsyncMock(return_value=True)
    adapter.close = AsyncMock()
    return adapter


def _make_fresh_db(tmp_path) -> tuple[SQLiteDatabase, IdempotencyStore]:
    """
    Create an initialised SQLite DB in tmp_path.
    Uses a synchronous event loop call so the sync fixture can await async init.
    """
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex}.db")
    db = SQLiteDatabase(db_path=db_path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(db.initialize())
    loop.close()
    store = IdempotencyStore(db)
    return db, store


@pytest.fixture()
def app_with_mocks(mock_adapter, tmp_path):
    """
    FastAPI TestClient with:
    • lifespan=False  → our injected app.state is the ONLY state (no second DB)
    • Fresh SQLite DB in a unique tmp file per test
    • Mocked WhatsApp adapter (no network)

    Yields (client, mock_adapter, db) so tests can inspect adapter call counts
    and the DB directly.
    """
    app = create_app()

    db, store = _make_fresh_db(tmp_path)

    # Inject components onto app.state BEFORE the TestClient starts.
    # lifespan=False means the lifespan context manager never runs, so our
    # injected state is not overwritten by a second initialisation.
    app.state.db = db
    app.state.adapter = mock_adapter
    app.state.idempotency = store
    app.state.worker_task = None

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, mock_adapter, db

    # Teardown: close DB on a fresh loop (TestClient has already closed its loop)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(db.close())
    loop.close()


# ── AC-1: Bot receives a message and sends a reply ────────────────────────────

class TestEndToEndReply:
    def test_text_message_triggers_reply(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        resp = _post_webhook(client, _wa_payload(text="random question"))
        assert resp.status_code == 200
        assert adapter.send_text.called or adapter.send_buttons.called

    def test_help_command_sends_buttons(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        resp = _post_webhook(client, _wa_payload(text="help"))
        assert resp.status_code == 200
        adapter.send_buttons.assert_called_once()

    def test_hello_triggers_help_menu(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        resp = _post_webhook(client, _wa_payload(text="hello"))
        assert resp.status_code == 200
        adapter.send_buttons.assert_called_once()

    def test_mark_read_called_after_reply(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        wamid = _unique_wamid()
        _post_webhook(client, _wa_payload(wamid=wamid, text="hi"))
        adapter.mark_read.assert_called_once_with(wamid)

    def test_non_text_message_sends_text_fallback(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        resp = _post_webhook(client, _wa_payload(msg_type="image"))
        assert resp.status_code == 200
        adapter.send_text.assert_called_once()

    def test_unknown_text_sends_fallback(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        resp = _post_webhook(client, _wa_payload(text="what is the weather?"))
        assert resp.status_code == 200
        adapter.send_text.assert_called_once()


# ── AC-2: Idempotency — no duplicate replies ──────────────────────────────────

class TestIdempotency:
    def test_same_wamid_sent_twice_only_one_reply(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        wamid = _unique_wamid()
        payload = _wa_payload(wamid=wamid, text="hello")

        # First delivery
        resp1 = _post_webhook(client, payload)
        assert resp1.status_code == 200
        first_count = adapter.send_buttons.call_count + adapter.send_text.call_count

        # WhatsApp retry — same wamid
        resp2 = _post_webhook(client, payload)
        assert resp2.status_code == 200
        second_count = adapter.send_buttons.call_count + adapter.send_text.call_count

        assert second_count == first_count, (
            f"Adapter called {second_count} times total; expected {first_count} "
            "(duplicate must not trigger a second reply)"
        )

    def test_different_wamids_both_get_replies(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(wamid=_unique_wamid(), text="hello"))
        _post_webhook(client, _wa_payload(wamid=_unique_wamid(), text="hello"))
        assert (adapter.send_buttons.call_count + adapter.send_text.call_count) == 2

    def test_duplicate_returns_200_not_error(self, app_with_mocks):
        """Duplicate must return 200 so WhatsApp stops retrying."""
        client, _, _ = app_with_mocks
        wamid = _unique_wamid()
        payload = _wa_payload(wamid=wamid, text="test")
        _post_webhook(client, payload)
        resp = _post_webhook(client, payload)
        assert resp.status_code == 200


# ── AC-3: /health returns meaningful status ───────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_200(self, app_with_mocks):
        client, _, _ = app_with_mocks
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_has_required_fields(self, app_with_mocks):
        client, _, _ = app_with_mocks
        data = client.get("/health").json()
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data
        assert "checks" in data

    def test_health_checks_idempotency_store_ok(self, app_with_mocks):
        client, _, _ = app_with_mocks
        data = client.get("/health").json()
        assert data["checks"].get("idempotency_store") == "ok"

    def test_health_checks_whatsapp_adapter_configured(self, app_with_mocks):
        client, _, _ = app_with_mocks
        data = client.get("/health").json()
        assert data["checks"].get("whatsapp_adapter") == "configured"

    def test_health_status_is_valid_value(self, app_with_mocks):
        client, _, _ = app_with_mocks
        data = client.get("/health").json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")


# ── AC-4: Help command and fallback ──────────────────────────────────────────

class TestHelpAndFallback:
    def test_help_buttons_are_sent(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(text="help"))
        call_args = adapter.send_buttons.call_args
        assert call_args is not None
        # buttons is the 3rd positional arg or kwarg
        buttons = call_args.kwargs.get("buttons") or (
            call_args.args[2] if len(call_args.args) > 2 else []
        )
        assert len(buttons) >= 1

    def test_slash_help_also_triggers_menu(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(text="/help"))
        adapter.send_buttons.assert_called_once()

    def test_start_triggers_help_menu(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(text="start"))
        adapter.send_buttons.assert_called_once()

    def test_fallback_text_is_non_empty(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(text="tell me a joke"))
        call_args = adapter.send_text.call_args
        assert call_args is not None
        text_sent = call_args.kwargs.get("text") or (
            call_args.args[1] if len(call_args.args) > 1 else ""
        )
        assert len(text_sent) > 0

    def test_non_text_fallback_is_non_empty(self, app_with_mocks):
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(msg_type="audio"))
        call_args = adapter.send_text.call_args
        assert call_args is not None
        text_sent = call_args.kwargs.get("text") or (
            call_args.args[1] if len(call_args.args) > 1 else ""
        )
        assert len(text_sent) > 0

    def test_case_insensitive_help(self, app_with_mocks):
        """'HELP' and 'Help' should both trigger the help menu."""
        client, adapter, _ = app_with_mocks
        _post_webhook(client, _wa_payload(text="HELP"))
        adapter.send_buttons.assert_called_once()
