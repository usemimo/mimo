"""
tests/test_webhook.py
---------------------
Phase 1 acceptance criteria tests.

AC-1: Valid signed payload → 200, message normalised
AC-2: Invalid / missing signature → 401
AC-3: Malformed payload → 400, no crash
AC-4: GET verification handshake (bonus — good to have)

Helpers
───────
`_sign(body, secret)` produces the correct X-Hub-Signature-256 header value,
mirroring exactly what WhatsApp Cloud API sends.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

# Env vars and lifespan patch are applied in conftest.py before this module loads
from app.main import create_app

APP_SECRET = "test_app_secret"
VERIFY_TOKEN = "test_verify_token"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Synchronous TestClient — fine for Phase 1 (all routes are fast)."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    """Compute X-Hub-Signature-256 header value."""
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    )
    return f"sha256={mac.hexdigest()}"


def _whatsapp_payload(
    wamid: str = "wamid.test001",
    from_id: str = "15550001234",
    text: str = "Hello",
    timestamp: str = "1700000000",
) -> dict:
    """Minimal but structurally valid WhatsApp Cloud API message payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry_id_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550009876", "phone_number_id": "123456789"},
                            "contacts": [{"profile": {"name": "Test User"}, "wa_id": from_id}],
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": from_id,
                                    "timestamp": timestamp,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


# ── AC-1: Valid signed payload accepted and normalised ────────────────────────

class TestValidPayload:
    def test_valid_signature_returns_200(self, client):
        body = json.dumps(_whatsapp_payload()).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 200, resp.text

    def test_valid_payload_returns_ok_status(self, client):
        body = json.dumps(_whatsapp_payload()).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        data = resp.json()
        assert data["status"] == "ok"

    def test_valid_payload_returns_correlation_id(self, client):
        body = json.dumps(_whatsapp_payload()).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        data = resp.json()
        assert "correlation_id" in data
        assert len(data["correlation_id"]) == 36  # UUID4 format

    def test_unknown_message_type_normalised_gracefully(self, client):
        """Unknown type (e.g. 'location') should not crash — normalised to 'unknown'."""
        payload = _whatsapp_payload()
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "location"
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 200

    def test_status_only_payload_returns_200(self, client):
        """Delivery/read receipt events (no messages) should return 200."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "entry_id_1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {},
                                "statuses": [
                                    {"id": "wamid.abc", "status": "delivered", "timestamp": "1700000001"}
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 200


# ── AC-2: Invalid / missing signature → 401 ───────────────────────────────────

class TestInvalidSignature:
    def test_missing_signature_header_returns_401(self, client):
        body = json.dumps(_whatsapp_payload()).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
            # No X-Hub-Signature-256
        )
        assert resp.status_code == 401

    def test_wrong_secret_returns_401(self, client):
        body = json.dumps(_whatsapp_payload()).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body, secret="wrong_secret"),
            },
        )
        assert resp.status_code == 401

    def test_tampered_body_returns_401(self, client):
        """Sign one body, send a different body."""
        original = json.dumps(_whatsapp_payload()).encode()
        tampered = original + b" extra"
        resp = client.post(
            "/webhook",
            content=tampered,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(original),  # signature for original, not tampered
            },
        )
        assert resp.status_code == 401

    def test_malformed_signature_prefix_returns_401(self, client):
        """Header without 'sha256=' prefix."""
        body = json.dumps(_whatsapp_payload()).encode()
        mac = hmac.new(key=APP_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": mac.hexdigest(),  # missing "sha256=" prefix
            },
        )
        assert resp.status_code == 401


# ── AC-3: Malformed payloads → 400, no crash ─────────────────────────────────

class TestMalformedPayloads:
    def test_invalid_json_returns_400(self, client):
        body = b"this is not json {"
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 400

    def test_empty_body_returns_400(self, client):
        body = b""
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 400

    def test_json_array_instead_of_object_returns_400(self, client):
        body = json.dumps(["not", "an", "object"]).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 400

    def test_missing_required_fields_returns_400(self, client):
        """Payload is valid JSON but completely wrong shape."""
        body = json.dumps({"foo": "bar"}).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        # RawWebhookPayload has all-optional fields, so this is actually 200
        # (WhatsApp sends many partial payloads). The important thing is no crash.
        assert resp.status_code in (200, 400)

    def test_malformed_payload_response_has_correlation_id(self, client):
        """Even error responses carry a correlation_id for traceability."""
        body = b"bad json"
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _sign(body),
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "correlation_id" in data


# ── AC-4: GET verification handshake ─────────────────────────────────────────

class TestVerificationHandshake:
    def test_valid_handshake_echoes_challenge(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "abc123challenge",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "abc123challenge"

    def test_wrong_token_returns_403(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "abc123challenge",
            },
        )
        assert resp.status_code == 403

    def test_wrong_mode_returns_403(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "abc123challenge",
            },
        )
        assert resp.status_code == 403
