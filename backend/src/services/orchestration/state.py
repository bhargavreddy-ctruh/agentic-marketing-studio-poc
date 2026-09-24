"""The shared state every LangGraph node reads/writes. Kept small and typed on purpose (KISS)."""
from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    session_id: str
    user_id: str | None
    user_message: str
    brief: dict[str, Any]
    route: str | None  # "dynamic" | "full_image" | "full_video" | "direct_fix" | None
    target_specialist: str | None  # set only for the direct_fix route
    dynamic_plan: list[dict[str, Any]] | None  # List of steps for dynamic execution
    result: dict[str, Any] | None
    error: str | None
    new_guardrails: list[dict[str, Any]] | None
