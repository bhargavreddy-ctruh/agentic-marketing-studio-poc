"""Correlation ID — created once at the edge, attached to every log line for this request."""
from __future__ import annotations

import uuid
from contextvars import ContextVar

from ..config import settings

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return _correlation_id.get()


class CorrelationIdMiddleware:
    """Real, live-found bug (2026-10-06): this used to subclass
    `starlette.middleware.base.BaseHTTPMiddleware` — a well-documented Starlette gotcha for any
    streaming endpoint. `BaseHTTPMiddleware.dispatch()` must have the complete `Response` object
    in hand before it can run code AFTER `call_next()` (here, setting a header on the response) —
    for a `StreamingResponse` like the SSE route (`GET /{session_id}/events`), that means
    buffering the ENTIRE stream internally before the client ever receives a single byte, since
    there's no other way to guarantee "after" code runs before the response is considered done.
    Confirmed live: ideation/routing/specialist events were all genuinely emitted in real time on
    the backend, but the browser only ever received them in one burst the instant the stream
    closed (`turn_completed`) — explaining a live-progress UI that looked permanently stuck on
    "Imagining..." right up until the final result appeared, even on turns that took 30+ seconds.

    Pure ASGI middleware (no `BaseHTTPMiddleware`) doesn't have this problem: it intercepts the
    raw `send` callable directly and forwards every message (including each individual streamed
    chunk) through immediately — the correlation ID header is added to the one `http.response.start`
    message as it passes through, never requiring the full body to be buffered first."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        cid = headers.get(settings.correlation_header.lower().encode(), b"").decode() or uuid.uuid4().hex
        token = _correlation_id.set(cid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers") or [])
                response_headers.append((settings.correlation_header.encode(), cid.encode()))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _correlation_id.reset(token)
