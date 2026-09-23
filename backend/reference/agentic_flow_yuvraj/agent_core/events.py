"""Shared agent event schema used by SSE streams across agentic features."""
from __future__ import annotations

from typing import Any, TypedDict


class AgentEvent(TypedDict, total=False):
    type: str
    agent: str
    message: str
    data: dict[str, Any]
