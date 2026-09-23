"""
logger.py -- Centralised logging configuration for Ctruh AI Creative Studio.

Usage:
    from logger import get_logger
    log = get_logger(__name__)

    log.debug("Detailed trace")
    log.info("Operational message")
    log.warning("Unexpected but recoverable")
    log.error("Failure with context", exc_info=True)

Log levels (set via LOG_LEVEL in .env, default INFO):
    DEBUG   -- verbose: full request payloads, Gemini response snippets
    INFO    -- default: start/complete per request, timing
    WARNING -- unexpected but non-fatal: retries, partial failures
    ERROR   -- failures and exceptions

Sensitive data policy:
    - GEMINI_API_KEY is never logged (masked to first 8 chars + ***)
    - Full base64 image/video data is never logged
    - Gemini raw responses are truncated to 120 chars max
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Sensitive data filter
# ─────────────────────────────────────────────────────────────────────────────
_PATTERNS = [
    (re.compile(r"(key=)[A-Za-z0-9\-_]{8,}"),             r"\1[REDACTED]"),
    (re.compile(r"(AIza)[A-Za-z0-9\-_]{30,}"),            r"[GEMINI_KEY_REDACTED]"),
    (re.compile(r'"data":\s*"[A-Za-z0-9+/=]{40,}"'),      r'"data": "[BASE64_REDACTED]"'),
    (re.compile(r'(bytesBase64Encoded":\s*")[A-Za-z0-9+/=]{40,}'), r'\1[BASE64_REDACTED]'),
]


class SensitiveDataFilter(logging.Filter):
    """Strips API keys and base64 blobs from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, replacement in _PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        if record.args:
            try:
                formatted = record.getMessage()
                for pattern, replacement in _PATTERNS:
                    formatted = pattern.sub(replacement, formatted)
                record.msg  = formatted
                record.args = None
            except Exception:
                pass
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────
_CONSOLE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s"
_FILE_FORMAT    = "%(asctime)s | %(levelname)-8s | %(name)-24s | %(filename)s:%(lineno)d | %(message)s"
_DATE_FORMAT    = "%Y-%m-%d %H:%M:%S"


# ─────────────────────────────────────────────────────────────────────────────
# Setup -- called once at import time
# ─────────────────────────────────────────────────────────────────────────────
def _setup() -> None:
    raw_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level     = getattr(logging, raw_level, logging.INFO)

    root = logging.getLogger("ctruh_studio")
    if root.handlers:
        return  # already configured (hot-reload guard)

    root.setLevel(level)
    root.propagate = False

    sensitive_filter = SensitiveDataFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
    console.addFilter(sensitive_filter)
    root.addHandler(console)

    log_dir = os.getenv("LOG_DIR", "")
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = Path(log_dir) / "ctruh_studio.log"
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
        fh.addFilter(sensitive_filter)
        root.addHandler(fh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


_setup()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """
    Returns a child logger under the 'ctruh_studio' namespace.

    Usage:
        log = get_logger(__name__)
    """
    clean = name.replace("backend.", "")
    return logging.getLogger(f"ctruh_studio.{clean}")


def mask_key(key: str | None, keep: int = 8) -> str:
    """Show only the first `keep` characters of an API key for safe logging."""
    if not key:
        return "[missing]"
    return key[:keep] + "***"
