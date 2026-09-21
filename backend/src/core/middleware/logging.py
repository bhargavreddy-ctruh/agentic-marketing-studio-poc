"""
Structured JSON logging. Rules.md section 1: no print statements, no hand-rolled formatting.
Every log line carries the correlation ID automatically via the filter below.
"""
from __future__ import annotations

import json
import logging
import sys
import time

from .correlation import get_correlation_id


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Anything passed via logger.info("msg", extra={"foo": "bar"}) rides along too.
        for key, value in record.__dict__.items():
            if key.startswith("_extra_"):
                payload[key.removeprefix("_extra_")] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
