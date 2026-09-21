"""
THE ONLY file that imports the langsmith SDK.

Traces every LangGraph node/tool/provider call with cost/latency — Architecture.md section 3:
this is the actual evidence for "good, cheap, fast," not a claim. `traceable` is re-exported here
so services decorate their node functions with `from providers.observability.langsmith import
traceable` rather than importing langsmith directly (genai_build's rule).
"""
from __future__ import annotations

import os

from langsmith import Client, traceable  # noqa: F401 — re-exported for services to use

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger

log = get_logger(__name__)


def configure_langsmith() -> None:
    """Set the env vars LangSmith's tracing machinery reads. Safe to call with no key set —
    tracing simply stays off, per the 'boot cleanly with placeholders' Phase 0 decision."""
    if settings.langsmith_api_key and settings.langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        log.warning("langsmith_disabled", extra={"_extra_reason": "no API key configured"})


async def verify_connection() -> bool:
    """Phase 0's exit criterion: confirm a real trace can reach LangSmith. Raises
    ProviderUnavailable with a clear reason if it can't — never silently 'looks fine'."""
    if not settings.langsmith_api_key:
        raise ProviderUnavailable("langsmith", "LANGSMITH_API_KEY is not set")

    @traceable(name="phase0_hello_world", project_name=settings.langsmith_project)
    def _hello() -> str:
        return "phase 0 scaffold verification"

    try:
        _hello()
    except Exception as exc:  # the one place a bare Exception is acceptable — see error_handler.py
        raise ProviderUnavailable("langsmith", f"trace call failed: {exc}") from exc

    log.info("langsmith_verified", extra={"_extra_project": settings.langsmith_project})
    return True
