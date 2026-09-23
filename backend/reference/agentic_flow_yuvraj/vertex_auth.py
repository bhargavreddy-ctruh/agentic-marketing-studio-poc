"""
vertex_auth.py -- Shared Vertex AI authentication with token caching.

Used by video_providers/vertex_video and fashion_tryon for Vertex API calls.
Caches credentials and token until near expiry to avoid redundant refreshes under load.
"""
from __future__ import annotations

import time

_cached_credentials = None
_cached_token_expiry = 0.0


def get_vertex_token() -> str:
    """
    Get Bearer token for Vertex AI (blocking, run via asyncio.to_thread).
    Cached until 60 seconds before expiry.
    """
    global _cached_credentials, _cached_token_expiry
    now = time.time()
    if _cached_credentials and now < _cached_token_expiry - 60:
        return _cached_credentials.token
    import google.auth
    import google.auth.transport.requests
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    _cached_credentials = credentials
    _cached_token_expiry = (
        credentials.expiry.timestamp() if credentials.expiry else now + 3600
    )
    return credentials.token
