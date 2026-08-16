"""
app/core/logging.py
-------------------
Structured, JSON-friendly logging setup.

Key design decisions
────────────────────
• Uses Python's stdlib logging — no third-party dependency.
• Every log record produced *inside a request* carries a `correlation_id`
  field that is injected via a contextvars.ContextVar.  This means Phase 3+
  never needs to retrofit correlation IDs — they are already in every line.
• In development the formatter produces human-readable output; in production
  it produces newline-delimited JSON suitable for Cloud Logging / Datadog.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ── Correlation ID context variable ───────────────────────────────────────────
# Set this at the start of every inbound request; all log calls within that
# async task will automatically pick it up.
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return correlation_id_var.get()


def set_correlation_id(cid: str) -> None:
    correlation_id_var.set(cid)


# ── Custom formatter ──────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": get_correlation_id(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _HumanFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    FMT = "%(asctime)s [%(levelname)-8s] [cid=%(correlation_id)s] %(name)s — %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        record.correlation_id = get_correlation_id()  # type: ignore[attr-defined]
        return super().format(record)

    def __init__(self) -> None:
        super().__init__(fmt=self.FMT, datefmt="%H:%M:%S")


# ── Public setup function ─────────────────────────────────────────────────────

def setup_logging(log_level: str = "INFO", app_env: str = "development") -> None:
    """
    Call once at application startup (from main.py lifespan).

    Parameters
    ----------
    log_level : str
        One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    app_env : str
        "development" → human-readable output on stdout.
        anything else  → JSON output on stdout.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    if app_env == "development":
        handler.setFormatter(_HumanFormatter())
    else:
        handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Silence noisy third-party loggers in production
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — use this instead of logging.getLogger() directly."""
    return logging.getLogger(name)
