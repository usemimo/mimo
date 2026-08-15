"""
app/core/security.py
--------------------
HMAC-SHA256 signature verification for WhatsApp Cloud API webhooks.

WhatsApp signs every POST body as:
    X-Hub-Signature-256: sha256=<hex_digest>

The digest is computed over the raw (bytes) request body using the app secret
as the key.  We must read the raw body before JSON parsing, then compare with
hmac.compare_digest (constant-time comparison to prevent timing attacks).

References
──────────
https://developers.facebook.com/docs/graph-api/webhooks/getting-started#verification-requests
"""

import hashlib
import hmac

from app.core.logging import get_logger

logger = get_logger(__name__)


def verify_whatsapp_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """
    Return True if the request body matches the X-Hub-Signature-256 header.

    Parameters
    ----------
    raw_body : bytes
        The unmodified request body bytes (read before any JSON parsing).
    signature_header : str | None
        The value of the X-Hub-Signature-256 header, e.g. "sha256=abc123...".
        If None or malformed, the function returns False immediately.
    app_secret : str
        The WhatsApp app secret loaded from environment variables.

    Returns
    -------
    bool
        True  → signature is valid; proceed with processing.
        False → signature is missing, malformed, or doesn't match; reject.
    """
    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning(
            "Malformed X-Hub-Signature-256 header (expected 'sha256=' prefix)",
            extra={"header_value": signature_header[:20]},
        )
        return False

    provided_hex = signature_header.removeprefix("sha256=")

    expected_mac = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    )
    expected_hex = expected_mac.hexdigest()

    # Constant-time comparison — prevents timing-based oracle attacks
    is_valid = hmac.compare_digest(expected_hex, provided_hex)

    if not is_valid:
        logger.warning(
            "Signature mismatch — request rejected",
            extra={
                "expected_prefix": expected_hex[:8] + "...",
                "provided_prefix": provided_hex[:8] + "..." if len(provided_hex) >= 8 else provided_hex,
            },
        )

    return is_valid
