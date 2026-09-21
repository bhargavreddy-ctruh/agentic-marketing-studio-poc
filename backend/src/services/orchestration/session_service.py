"""
Ties a session's persistence (via the SessionRepository/CanvasRepository protocols) to running the
LangGraph graph. This is business logic — it belongs in services/, not in a route handler
(Rules.md section 2).
"""
from __future__ import annotations

import asyncio
import uuid

from ...core.events import emit, mark_turn_done, set_current_session, start_new_turn
from ...core.exceptions import NotFoundError, ValidationFailed
from ...mappers.session_mapper import SessionMapper
from ...models.base import async_session_factory
from ...models.canvas_element import CanvasElementModel
from ...models.session import SessionModel
from ...repositories.base import CanvasRepository, SessionRepository
from ...repositories.sqlite.sqlite_canvas_repository import SqliteCanvasRepository
from ...schemas.sessions.responses import IdeationOption, IdeationPrompt, SessionResponse
from ..compliance.compliance_gate import run_compliance_gate
from .graph import get_graph

# Real, live-found asyncio gotcha (2026-09-21): a bare `asyncio.create_task(...)` with no
# reference held anywhere can be garbage-collected mid-run, silently killing the background QA
# check before it ever persists a result — a task's only strong reference by default is this
# variable holding it. Kept at module scope (not per-instance) since SessionService itself is
# constructed fresh per request; the task must outlive that.
_background_tasks: set[asyncio.Task] = set()


class SessionService:
    def __init__(self, sessions: SessionRepository, canvas: CanvasRepository):
        # Depends on the Protocols, never a concrete SQLite class (Dependency Inversion).
        self._sessions = sessions
        self._canvas = canvas

    async def start_session(self, initial_message: str, *, approval_mode: str = "auto") -> SessionResponse:
        session = SessionModel(
            id=uuid.uuid4().hex, status="ideating", brief={}, approval_mode=approval_mode
        )
        session = await self._sessions.add(session)
        return await self._run_turn(session, user_message=initial_message)

    async def post_turn(
        self,
        session_id: str,
        *,
        picked_option_id: str | None,
        free_text: str | None,
        referenced_element_id: str | None = None,
    ) -> SessionResponse:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError("Session", session_id)
        if not picked_option_id and not free_text:
            raise ValidationFailed("Provide either picked_option_id or free_text")

        # A card pick is just shorthand for its label — the graph's ideation node only deals in
        # plain text either way (Architecture.md section 1d: a pick is a shortcut, not a
        # different code path).
        user_message = free_text or (session.brief.get("_last_option_labels", {}) or {}).get(
            picked_option_id, picked_option_id
        )
        return await self._run_turn(
            session, user_message=user_message, referenced_element_id=referenced_element_id
        )

    async def get_session(self, session_id: str) -> SessionResponse:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError("Session", session_id)
        return SessionMapper.to_response(session)

    async def _run_turn(
        self, session: SessionModel, *, user_message: str, referenced_element_id: str | None = None
    ) -> SessionResponse:
        # Sets the ContextVar every nested call (Leads, specialists, tools) reads to emit live
        # events for THIS turn, without session_id being threaded through every function
        # signature in between (core/events.py, same pattern as correlation.py's correlation ID).
        set_current_session(session.id)
        start_new_turn(session.id)
        emit("turn_started", user_message=user_message)
        try:
            return await self._run_turn_inner(
                session, user_message=user_message, referenced_element_id=referenced_element_id
            )
        finally:
            await mark_turn_done(session.id)

    async def _run_turn_inner(
        self, session: SessionModel, *, user_message: str, referenced_element_id: str | None = None
    ) -> SessionResponse:
        # The direct_fix route needs something to act on — the most recently produced element by
        # default (Memory.md, Phase 3 conformance audit), or the one the user explicitly picked in
        # the UI ("reference an element in chat") when they named one that's still real — a stale
        # or unknown id just falls back to the default rather than erroring the whole turn.
        existing_elements = await self._canvas.list_for_session(session.id)
        latest_element = existing_elements[-1] if existing_elements else None
        if referenced_element_id:
            referenced = next((e for e in existing_elements if e.id == referenced_element_id), None)
            if referenced is not None:
                latest_element = referenced
        brief_for_graph = dict(session.brief)
        # Read-only, sourced from the session's own column, never persisted back into brief JSON
        # (Memory.md, Phase 4: "approve" mode's per-stage pipeline gates).
        brief_for_graph["approval_mode"] = session.approval_mode
        if latest_element:
            brief_for_graph["latest_element_id"] = latest_element.id
            brief_for_graph["latest_element_storage_ref"] = latest_element.storage_ref
            brief_for_graph["latest_element_type"] = latest_element.element_type
            # The original generation prompt, not just the brief's overall campaign idea — a
            # text-only specialist has no memory of its own prior run and no way to see the
            # storage_ref's pixels, so the exact original description is real grounding a general
            # "campaign idea so far" text can't fully substitute for (Memory.md, Phase 4: the same
            # real bug found in regenerate/comment resolution — without this, a direct_fix could
            # equally invent an unrelated image).
            meta = latest_element.metadata_json or {}
            brief_for_graph["latest_element_description"] = (
                meta.get("image_prompt") or meta.get("frame_prompt") or meta.get("motion_prompt")
            )

        graph = get_graph()
        result_state = await graph.ainvoke(
            {"session_id": session.id, "user_message": user_message, "brief": brief_for_graph}
        )

        # Only the fields ideation/orchestrator actually mutate belong in the persisted brief —
        # the latest-element scratch fields above are graph-input-only, not part of the session's
        # own accumulated state.
        returned_brief = result_state.get("brief") or session.brief
        _scratch_keys = (
            "latest_element_id", "latest_element_storage_ref", "latest_element_type",
            "latest_element_description", "approval_mode",
        )
        session.brief = {k: v for k, v in returned_brief.items() if k not in _scratch_keys}
        result = result_state.get("result")

        prompt: IdeationPrompt | None = None
        if result and result.get("options") is not None:
            # Still ideating — remember option labels so a later pick-by-id can resolve to text.
            # Reassigned as a new dict (not mutated in place) so SQLAlchemy's change tracking on
            # the JSON column reliably picks it up.
            session.brief = {
                **session.brief,
                "_last_option_labels": {
                    opt["id"]: opt["label"] for opt in result.get("options", [])
                },
            }
            # "awaiting_approval" for a real Phase 4 pipeline-stage gate (distinguishable from
            # genuinely still-ideating), reusing the exact same message+options+free-text shape.
            session.status = "awaiting_approval" if session.brief.get("video_stage") else "ideating"
            prompt = self._to_ideation_prompt(result)
        elif result and result.get("storage_ref"):
            session.status = "completed"
            element_id: str | None = None
            if result.get("update_existing_element_id"):
                # A direct_fix that adjusted the existing element in place — bump its version
                # rather than creating an unrelated new one (per-element versioning, Architecture.md
                # section 1c), keeping the same id so a client following that element sees the fix.
                target = await self._canvas.get_element(result["update_existing_element_id"])
                if target is not None:
                    target.storage_ref = result["storage_ref"]
                    target.metadata_json = {**target.metadata_json, **result.get("metadata", {})}
                    target.version += 1
                    target = await self._canvas.update_element(target)
                    element_id = target.id
                else:
                    element_id = (await self._add_new_element(session.id, result)).id
            else:
                element_id = (await self._add_new_element(session.id, result)).id

            # Real QA, kicked off automatically right after generation — a live-found gap
            # (2026-09-21): `run_compliance_gate` already existed and worked, but nothing in the
            # real user-facing flow ever called it. NOT awaited here on purpose — per the user's
            # own explicit ask, the element should appear on canvas immediately (already
            # `compliance_status: "running"` by the model's own default) while QA runs
            # afterward, rather than the whole turn waiting on it and only showing the element
            # once QA has already finished.
            self._schedule_compliance_check(element_id)
        elif result:
            # A placeholder or error result — surface the message, nothing to persist yet.
            session.status = "error" if result_state.get("error") else "pending"
            prompt = IdeationPrompt(message=result.get("message", ""), options=[], allow_free_text=True)

        session.next_prompt_json = prompt.model_dump() if prompt else None
        session = await self._sessions.update(session)
        emit("turn_completed", status=session.status)
        return SessionMapper.to_response(session)

    async def _add_new_element(self, session_id: str, result: dict) -> CanvasElementModel:
        element = CanvasElementModel(
            id=uuid.uuid4().hex,
            session_id=session_id,
            element_type=result.get("element_type", "image"),
            produced_by_specialist=result.get("produced_by_specialist", "unknown"),
            storage_ref=result["storage_ref"],
            metadata_json=result.get("metadata", {}),
        )
        return await self._canvas.add_element(element)

    def _schedule_compliance_check(self, element_id: str) -> None:
        """Fires the real compliance gate as a genuinely detached background task — the caller
        (the current turn) does not await this, so the HTTP response returns immediately with the
        element already visible on canvas at its default `compliance_status: "running"`."""
        task = asyncio.create_task(_run_compliance_background(element_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    @staticmethod
    async def _run_compliance_and_persist(element_id: str) -> None:
        # Kept as a thin, awaitable wrapper around the real module-level worker below — some
        # callers (tests, a future synchronous caller) may still want to await the real result
        # directly rather than fire-and-forget it.
        await _run_compliance_background(element_id)

    @staticmethod
    def _to_ideation_prompt(result: dict) -> IdeationPrompt:
        options = [IdeationOption(**opt) for opt in result.get("options", [])]
        return IdeationPrompt(
            message=result.get("message", ""),
            options=options,
            allow_free_text=result.get("allow_free_text", True),
        )


async def _run_compliance_background(element_id: str) -> None:
    """The real compliance check, run completely independently of whichever HTTP request
    triggered it — this keeps running after that request's own response has already been sent,
    so it needs its own DB session rather than the request-scoped one `SessionService` was built
    with (which closes once the request ends). Same pattern already used by tools that run
    outside any one request's own session lifecycle (e.g. `discount_claims_calculator.py`).

    A real QA failure never surfaces as a 500 to anyone, because there's no request left waiting
    on this by the time it runs — but an infra error (a provider hiccup, a missing asset) is still
    written as "failed", a real, visible flag, rather than leaving the element stuck showing
    "running" forever (worse than an occasional false "failed", which the gate's own remediation
    already makes rare in practice)."""
    async with async_session_factory() as db:
        canvas = SqliteCanvasRepository(db)
        try:
            gate_result = await run_compliance_gate(canvas=canvas, element_id=element_id)
            status = "passed" if gate_result.get("overall_passed") else "failed"
        except Exception:  # noqa: BLE001 — deliberately broad: this must never crash a bg task silently stuck
            status = "failed"

        # Remediation (inside the gate) may have already bumped the element's storage_ref/version —
        # re-fetch rather than trust a stale local copy before writing the final verdict.
        element = await canvas.get_element(element_id)
        if element is not None:
            element.compliance_status = status
            await canvas.update_element(element)
