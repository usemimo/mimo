"""
app/adapters/whatsapp.py
------------------------
WhatsApp Cloud API adapter — all outbound calls live here.

Design decisions
────────────────
• Two SEPARATE code paths for free-form text and template sends.
  Rationale: WhatsApp's 24-hour customer-service window means free-form
  messages only work within 24h of the user's last message.  Outside that
  window only approved templates work.  The full window-detection logic
  arrives in a later phase — but by keeping the code paths separate *now*,
  that logic has somewhere to live without a rewrite.

• The adapter never raises to callers.  Network / API failures are logged
  with the correlation_id from context and the method returns None / False.
  The webhook handler still returns 200 to WhatsApp.

• Mark-read is best-effort: failure is logged but does not affect message
  processing.

API reference
─────────────
https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_correlation_id, get_logger

logger = get_logger(__name__)

_WA_API_BASE = "https://graph.facebook.com/v21.0"


@dataclass
class ButtonOption:
    """A single interactive reply button."""
    id: str    # max 256 chars, used to identify which button was pressed
    title: str # max 20 chars, displayed to the user


class WhatsAppAdapter:
    """
    HTTP client for the WhatsApp Cloud API.

    Instantiated once at startup (stored on app.state) and reused across
    requests so the underlying httpx connection pool is shared.

    Parameters
    ----------
    phone_number_id : str
        Your WhatsApp Business phone number ID.
    access_token : str
        Long-lived or temporary API access token.
    timeout : float
        Per-request timeout in seconds.  Default 10s.
    """

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        timeout: float = 10.0,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._base_url = f"{_WA_API_BASE}/{phone_number_id}/messages"
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout
        # Shared async client — connection pool reused across requests
        self._client = httpx.AsyncClient(timeout=timeout)

    # ── Free-form text (within 24h window) ───────────────────────────────────

    async def send_text(self, to: str, text: str) -> bool:
        """
        Send a plain text message.

        Only works within the 24-hour customer-service window.
        Use send_template() outside that window.

        Returns True on success, False on any error.
        """
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        }
        return await self._post(payload, action="send_text", to=to)

    # ── Interactive buttons (within 24h window) ───────────────────────────────

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[ButtonOption],
        header: str | None = None,
        footer: str | None = None,
    ) -> bool:
        """
        Send an interactive button message (max 3 buttons).

        Only works within the 24-hour customer-service window.

        Parameters
        ----------
        to      : recipient phone number
        body    : main message text (required)
        buttons : list of ButtonOption — max 3, title max 20 chars
        header  : optional header text
        footer  : optional footer text (e.g. "Powered by AI")
        """
        if len(buttons) > 3:
            logger.warning("send_buttons: clamping to first 3 buttons (WA limit)")
            buttons = buttons[:3]

        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": b.title[:20]}}
                    for b in buttons
                ]
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}
        if footer:
            interactive["footer"] = {"text": footer}

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self._post(payload, action="send_buttons", to=to)

    # ── Template send (works outside 24h window) ──────────────────────────────

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en_US",
        components: list[dict[str, Any]] | None = None,
    ) -> bool:
        """
        Send an approved template message.

        Works outside the 24-hour customer-service window.
        Templates must be approved in Meta's template manager first.

        NOTE: This is a SEPARATE code path from send_text() intentionally.
        The 24-hour window detection logic (Phase 5+) will call either
        send_text() or send_template() based on conversation recency.
        Do not merge these code paths.

        Parameters
        ----------
        to            : recipient phone number
        template_name : approved template name (e.g. "hello_world")
        language_code : BCP-47 code (e.g. "en_US", "ar")
        components    : optional list of template components (header/body/buttons)
        """
        template_payload: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template_payload["components"] = components

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": template_payload,
        }
        return await self._post(payload, action="send_template", to=to)

    # ── Delivery / read receipts ──────────────────────────────────────────────

    async def mark_read(self, wamid: str) -> bool:
        """
        Send a read receipt for the given message ID.

        Best-effort — failure is logged but does not affect processing.
        """
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wamid,
        }
        return await self._post(payload, action="mark_read", to="<receipt>")

    # ── Internal HTTP helper ──────────────────────────────────────────────────

    async def _post(
        self, payload: dict[str, Any], *, action: str, to: str
    ) -> bool:
        """
        POST to the WA Cloud API.  Logs all errors.  Never raises.

        Returns True on HTTP 200, False on any failure.
        """
        cid = get_correlation_id()
        try:
            response = await self._client.post(
                self._base_url,
                json=payload,
                headers=self._headers,
            )
            if response.is_success:
                logger.info(
                    f"WhatsApp API call succeeded",
                    extra={"action": action, "to": to, "status": response.status_code},
                )
                return True
            else:
                logger.error(
                    "WhatsApp API call failed",
                    extra={
                        "action": action,
                        "to": to,
                        "status": response.status_code,
                        "body": response.text[:200],
                        "correlation_id": cid,
                    },
                )
                return False
        except httpx.TimeoutException:
            logger.error(
                "WhatsApp API call timed out",
                extra={"action": action, "to": to, "correlation_id": cid},
            )
            return False
        except Exception as exc:
            logger.error(
                "WhatsApp API call raised unexpected error",
                extra={"action": action, "to": to, "correlation_id": cid},
                exc_info=exc,
            )
            return False

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
