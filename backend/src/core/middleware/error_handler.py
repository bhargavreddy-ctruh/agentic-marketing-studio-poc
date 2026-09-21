"""Typed exception -> HTTP response. This is the ONLY place an AppError gets turned into JSON."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..exceptions import AppError
from .correlation import get_correlation_id
from .logging import get_logger

log = get_logger(__name__)


def register_error_handlers(app: FastAPI) -> None:
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
        # is caught, and it always re-surfaces with full detail in the log.
        log.error("unhandled_exception", exc_info=exc, extra={"_extra_path": str(request.url.path)})
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "message": "Something went wrong.",
                "correlation_id": get_correlation_id(),
            },
        )
