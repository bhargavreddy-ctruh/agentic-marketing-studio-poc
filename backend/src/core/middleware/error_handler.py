"""Typed exception -> HTTP response. This is the ONLY place an AppError gets turned into JSON."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from ..exceptions import AppError
from .correlation import get_correlation_id
from .logging import get_logger

log = get_logger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    # Real, live-found incident (2026-10-06): a long, retry-heavy turn (Groq rate-limiting forcing
    # a slower provider fallback) held every connection in the small pool (`models/base.py`'s
    # `pool_size`/`max_overflow`) for 80+ real seconds — long enough that a completely unrelated,
    # concurrent request (the frontend's own periodic canvas-state refresh) waited the full 30s
    # pool checkout timeout and fell through to the generic `Exception` handler below: a bare
    # "Something went wrong" 500, while the turn's own real generation was succeeding the whole
    # time. `sqlalchemy.exc.TimeoutError` is NOT a subclass of the builtin `TimeoutError` (confirmed
    # live), so it's never already caught by anything else — a distinct, specific handler here
    # gives the client a real, retryable 503 instead of an opaque, non-actionable 500, and keeps
    # this failure mode visibly distinct in logs from a genuine application bug.
    @app.exception_handler(PoolTimeoutError)
    async def handle_pool_timeout(request: Request, exc: PoolTimeoutError) -> JSONResponse:
        log.warning(
            "db_pool_exhausted",
            extra={"_extra_path": str(request.url.path), "_extra_error": str(exc)},
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "ServiceBusy",
                "message": "Server is busy right now — please try again in a moment.",
                "correlation_id": get_correlation_id(),
            },
        )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "request_failed",
            extra={
                "_extra_path": str(request.url.path),
                "_extra_error_type": type(exc).__name__,
                "_extra_status_code": exc.status_code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": type(exc).__name__,
                "message": exc.message,
                "correlation_id": get_correlation_id(),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Deliberately generic response body (never leak internals), but a real stack trace in
        # the log — Rules.md: "no except Exception: pass". This is the one place a bare Exception
        # is caught, and it always re-surfaces with full detail in the log. (A blocking `open()`
        # file write used to duplicate this here too — removed: it blocked the event loop on every
        # unhandled exception, and `exc_info=exc` below already gives the structured logger the
        # full traceback, so nothing was actually gained by also hand-writing one to a flat file.)
        log.error("unhandled_exception", exc_info=exc, extra={"_extra_path": str(request.url.path)})
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "message": "Something went wrong.",
                "correlation_id": get_correlation_id(),
            },
        )
