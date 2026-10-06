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
    # The normalized plan preview `orchestrator.py`'s `_build_plan_preview()` builds for EVERY
    # route (2026-10-06) — MUST be declared here, not just set on the `state` dict inside
    # `route()`: LangGraph uses this TypedDict's own declared fields to set up its internal state
    # channels, so a key missing from this schema doesn't reliably survive being merged from one
    # graph node into the next (a real, live-found bug — confirmed via a live turn where the
    # `plan_proposed` SSE event correctly carried the plan, but `session_service.py`'s
    # `result_state.get("plan_preview")` still came back `None` at turn-completion time, because
    # this field was never declared here).
    plan_preview: list[dict[str, Any]] | None
    result: dict[str, Any] | None
    error: str | None
    new_guardrails: list[dict[str, Any]] | None
    # Real, live-found gap (2026-09-30): a specialist facing genuine ambiguity had no way to pause
    # and truly resume — every "pause" ended the turn and the next message restarted the whole
    # plan from scratch. Set by `_direct_fix_node`/`_dynamic_executor_node` when a step raises
    # `SpecialistNeedsClarification`; persisted into `session.brief["paused_plan"]` by
    # `session_service.py` so the SAME plan/step can resume with the user's real answer instead of
    # restarting. Shape: {"route": "direct_fix" | "dynamic", "specialist_name": str (direct_fix),
    # "plan": list[dict] (dynamic), "next_step_index": int (dynamic),
    # "completed_results": dict (dynamic, the executor's own accumulator), "question": str,
    # "options": list[dict] | None}.
    paused_plan: dict[str, Any] | None
