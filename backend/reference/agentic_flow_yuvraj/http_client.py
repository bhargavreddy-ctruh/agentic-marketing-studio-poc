"""
http_client.py -- Shared httpx.AsyncClient instances for connection reuse.

Providers use these instead of creating per-request clients. Initialized at
first use, closed on app shutdown via lifespan.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

# Shared clients per use-case (different timeouts)
_default_client: httpx.AsyncClient | None = None
_medium_client: httpx.AsyncClient | None = None  # 120s for try-on
_long_client: httpx.AsyncClient | None = None  # 180s for video URI fetch


def get_default_client() -> httpx.AsyncClient:
    """Client with 60s timeout for typical API calls."""
    global _default_client
    if _default_client is None:
        _default_client = httpx.AsyncClient(timeout=60.0)
    return _default_client


def get_medium_client() -> httpx.AsyncClient:
    """Client with 120s timeout for try-on and image generation."""
    global _medium_client
    if _medium_client is None:
        _medium_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200),
        )
    return _medium_client


def get_long_client() -> httpx.AsyncClient:
    """Client with 180s timeout for video URI fetches."""
    global _long_client
    if _long_client is None:
        _long_client = httpx.AsyncClient(timeout=180.0)
    return _long_client


async def close_all() -> None:
    """Close all shared clients. Call from app lifespan shutdown."""
    global _default_client, _medium_client, _long_client
    if _default_client is not None:
        await _default_client.aclose()
        _default_client = None
        log.debug("Closed shared default httpx client")
    if _medium_client is not None:
        await _medium_client.aclose()
        _medium_client = None
        log.debug("Closed shared medium httpx client")
    if _long_client is not None:
        await _long_client.aclose()
        _long_client = None
        log.debug("Closed shared long httpx client")
