"""
Ties a session's persistence (via the SessionRepository/CanvasRepository protocols) to running the
LangGraph graph. This is business logic — it belongs in services/, not in a route handler
(Rules.md section 2).
"""
from __future__ import annotations

import asyncio
import re
import uuid

from ...core.approval import is_approval, is_cancel
from ...core.config import settings
from ...core.element_descriptions import NO_DESCRIPTION_SENTINEL
from ...core.events import (
    cleanup_session,
    emit,
    get_current_turn_events,
    get_current_turn_thinking,
    mark_turn_done,
    set_current_session,
    start_new_turn,
)
from ...core.exceptions import Forbidden, NotFoundError, ValidationFailed
from ...core.middleware.logging import get_logger
from ...mappers.session_mapper import SessionMapper
from ...models.base import async_session_factory
from ...models.canvas_element import CanvasElementModel
from ...models.chat_turn import ChatTurnModel
from ...models.session import SessionModel
from ...repositories.base import CanvasRepository, ChatTurnRepository, SessionRepository
from ...repositories.postgres.postgres_canvas_repository import PostgresCanvasRepository
from ...schemas.sessions.responses import (
    ChatTurnResponse,
    IdeationOption,
    IdeationPrompt,
    SessionResponse,
)
from ..canvas.versioning_service import CanvasVersioningService
from ..compliance.compliance_gate import run_compliance_gate
from .graph import _direct_fix_node, _dynamic_executor_node, get_graph
from .state import GraphState

# Real, live-found asyncio gotcha (2026-09-21): a bare `asyncio.create_task(...)` with no
# reference held anywhere can be garbage-collected mid-run, silently killing the background QA
# check before it ever persists a result — a task's only strong reference by default is this
# variable holding it. Kept at module scope (not per-instance) since SessionService itself is
# constructed fresh per request; the task must outlive that.
_background_tasks: set[asyncio.Task] = set()

# Product/Brand crawler turn auto-detection (2026-09-28) — a plain URL regex, not a full RFC 3986
# parser: good enough to catch a pasted link in chat without pulling in a heavier dependency.
_URL_RE = re.compile(r"https?://[^\s]+")
_RUNNING_TURNS: dict[str, asyncio.Task] = {}
log = get_logger(__name__)

def cancel_running_turn(session_id: str) -> bool:
    """Cancels the currently running _run_turn task for the given session_id, if any."""
    task = _RUNNING_TURNS.get(session_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def _run_turn_bg(
    *,
    session_id: str,
    user_message: str,
    referenced_element_ids: list[str] | None,
    target_product_id: str | None,
    start_new_product: bool,
) -> None:
    """Module-level coroutine for fire-and-forget turn execution (2026-10-06).

    Creates a completely independent SessionService with its own async DB session so it can
    safely run after the originating HTTP request has already returned.  Progress is surfaced
    exclusively via SSE (core/events.py); the frontend fetches GET /sessions/{id} once the
    SSE 'turn_completed' event arrives to get the final SessionResponse.
    """
    from ...models.base import async_session_factory
    from ...repositories.postgres.postgres_canvas_repository import PostgresCanvasRepository
    from ...repositories.postgres.postgres_canvas_version_repository import (
        PostgresCanvasVersionRepository,
    )
    from ...repositories.postgres.postgres_chat_turn_repository import PostgresChatTurnRepository
    from ...repositories.postgres.postgres_session_repository import PostgresSessionRepository
    from ..canvas.versioning_service import CanvasVersioningService

    # Real, live-found gap (2026-10-06): `_run_turn` below already guarantees `mark_turn_done` and
    # a terminal status for anything that goes wrong INSIDE it — but this function's own session
    # lookup, and the `async with` block's own commit/close on the way out, sit OUTSIDE that guard.
    # An exception here used to propagate straight out of this fire-and-forget `asyncio.create_task`
    # with nothing to catch it but Python's silent "Task exception was never retrieved" — no
    # `mark_turn_done`, no status reset, leaving any client polling or listening for completion
    # waiting forever for a signal that would never come, with zero indication in the logs of why.
    try:
        async with async_session_factory() as db:
            svc = SessionService(
                sessions=PostgresSessionRepository(db),
                canvas=PostgresCanvasRepository(db),
                versioning=CanvasVersioningService(
                    canvas=PostgresCanvasRepository(db),
                    versions=PostgresCanvasVersionRepository(db),
                ),
                chat_turns=PostgresChatTurnRepository(db),
            )
            session = await svc._sessions.get(session_id)
            if session is None:
                log.error("_run_turn_bg: session not found", extra={"_extra_session": session_id})
                return
            await svc._run_turn(
                session,
                user_message=user_message,
                referenced_element_ids=referenced_element_ids,
                target_product_id=target_product_id,
                start_new_product=start_new_product,
            )
    except Exception as exc:
        # `_run_turn` already resolved its OWN failures (sets status="error", emits
        # "turn_completed", calls mark_turn_done) before re-raising — reaching here means the
        # failure is in code `_run_turn` never got a chance to guard (the session lookup above, or
        # the `async with` block's own teardown). Best-effort terminal status write, in its own
        # try/except so a second failure here can't suppress the `mark_turn_done` below — a client
        # waiting on EITHER signal (status polling or the SSE event) must still get unblocked.
        log.error("run_turn_bg_crashed_outside_guard", extra={"_extra_error": str(exc)})
        try:
            async with async_session_factory() as db:
                recovery_session = await PostgresSessionRepository(db).get(session_id)
                if recovery_session is not None:
                    recovery_session.status = "error"
                    recovery_session.next_prompt_json = IdeationPrompt(
                        message=f"Something went wrong while generating: {exc}",
                        options=[], allow_free_text=True,
                    ).model_dump()
                    await PostgresSessionRepository(db).update(recovery_session)
        except Exception as recovery_exc:
            log.error("run_turn_bg_recovery_failed", extra={"_extra_error": str(recovery_exc)})
    finally:
        await mark_turn_done(session_id)



def _resolve_yes_no_reply(reply: str, option_labels: dict[str, str]) -> str | None:
    """See `post_turn`'s own comment for the real bug this fixes. Only ever resolves when the
    reply is unambiguously a yes/no-shaped word (`is_approval`/`is_cancel`) AND one of the pending
    options' own label genuinely reads as the matching yes/no answer — never guesses among options
    that don't have that shape (most ideation choices are subject/style picks, not yes/no)."""
    if is_approval(reply):
        target_prefixes = ("yes", "approve")
    elif is_cancel(reply):
        target_prefixes = ("no", "reject", "cancel")
    else:
        return None
    for label in option_labels.values():
        if label.strip().lower().startswith(target_prefixes):
            return label
    return None


# Real, live-found bug (2026-09-24, per an explicit user ask: "is chat memory persistent and
# updated and try again and other options work properly with context and memory"): "retry" is
# offered as a real option after nearly every failure path (`graph.py`, 7 separate spots, always
# `{"id": "retry", "label": "Try again"}`), but clicking it resolved `user_message` to the literal
# label text "Try again" — which then went EVERYWHERE downstream as if it were the user's real
# request: the orchestrator's own classification context, `ideation_service.py`'s brief-merge
# (this is the exact, previously-flagged-but-unfixed root cause of `brief.idea` getting corrupted
# to `"Try again\n\nText description of the image"` — a weak fallback model handed nothing but the
# word "Try again" to synthesize from), the persisted `chat_turns.user_text` row (polluting the
# rolling 6-turn `_recent_chat_history` window with content-free entries), and the semantic
# LlamaIndex memory (`chat_memory_service.py` — indexing "User Request: Try again" helps no future
# retrieval and dilutes real results). `orchestrator.py`'s own prompt and `ideation_service.py`'s
# own "Context Guardrail" already acknowledged this exact risk for the worst case (zero context at
# all) — this fixes the more common degraded case where real context exists but gets replaced by
# a content-free placeholder anyway.
_BARE_RETRY_LABEL = "try again"


def _find_real_user_message_and_refs(past_turns: list) -> tuple[str | None, list[str] | None]:
    """Walks the session's own real chat history backward for the most recent turn that wasn't
    ITSELF just a bare "Try again" — so retrying a retry still recovers the real original request,
    not the previous retry's own placeholder text. Also recovers that SAME turn's
    `referenced_element_ids` (2026-09-25, a real, live-found gap: the field is a real persisted
    JSON column on `ChatTurnModel`, but nothing previously read it back — the frontend's own
    reference chip is cleared right after a turn is sent, so a "Try again" after a failed turn
    always resent an empty list, silently losing the reference the original request was about, and
    this function only ever recovered the TEXT half of that same turn)."""
    for turn in reversed(past_turns):
        text = (turn.user_text or "").strip()
        if text and text.lower() != _BARE_RETRY_LABEL:
            return text, (getattr(turn, "referenced_element_ids", None) or None)
    return None, None


class SessionService:
    def __init__(
        self,
        sessions: SessionRepository,
        canvas: CanvasRepository,
        versioning: CanvasVersioningService,
        chat_turns: ChatTurnRepository,
    ):
        # Depends on the Protocols, never a concrete SQLite class (Dependency Inversion).
        self._sessions = sessions
        self._canvas = canvas
        self._chat_turns = chat_turns
        # Real, live-found gap (2026-09-21): a chat-driven direct_fix used to mutate an element's
        # storage_ref directly, bumping `version` by hand with no row ever written to
        # CanvasElementVersionModel — the exact undo/redo/version-history infrastructure this
        # project already built for Regenerate/Comment/Direct-edit (canvas/routes.py's
        # /undo,/redo,/versions endpoints) silently didn't apply to chat edits at all, so a user
        # asking to modify an image via chat had no way back to the previous version. Routing
        # through the same CanvasVersioningService every other edit path already uses fixes that
        # for free, and also makes chat edits respect "approve" mode's staging like every other
        # edit type already does (previously chat edits always applied immediately regardless).
        self._versioning = versioning

    async def create_session(
        self, *, user_id: str, approval_mode: str = "approve", title: str | None = None
    ) -> SessionResponse:
        """Persists a bare session row and returns immediately, with no turn run yet.

        Split out from running the first turn (2026-09-21, real live-testing bug: the client
        can't open the SSE stream (`GET /{id}/events`) until it has a session_id, so the old
        one-call `start_session` that created the row AND ran the first turn synchronously meant
        the very first turn's `llm_delta`/node events were emitted into a queue nobody was
        listening to yet, and got silently discarded by the next turn's `start_new_turn()`. Node
        Mode and streamed "thinking" were therefore always empty for a session's first message —
        the most common turn there is. Splitting lets the client create the session, open the
        stream, THEN call `post_turn` for the first message, so turn 1 streams exactly like every
        later turn.

        `user_id` (Tasks_Workflows.md #2): every session is now a real, owned "workflow" — the
        route handler gets it from `CurrentUserDep`, never trusted from request body."""
        kwargs = {"id": uuid.uuid4().hex, "status": "ideating", "brief": {}, "approval_mode": approval_mode, "user_id": user_id}
        if title:
            kwargs["title"] = title
        session = SessionModel(**kwargs)
        session = await self._sessions.add(session)
        return SessionMapper.to_response(session)

    async def update_approval_mode(
        self, session_id: str, *, user_id: str, approval_mode: str
    ) -> SessionResponse:
        """Real, live-found gap (2026-09-24, per an explicit user ask): `approval_mode` was only
        ever settable at session creation — switching between "auto" and "approve" mid-conversation
        meant starting a brand-new session. Safe to change at any point: every real gate that reads
        it (`brief_for_graph["approval_mode"]` in `_run_turn_inner`, `CanvasVersioningService`'s
        apply-vs-stage branch) reads the session's CURRENT value fresh on each turn/edit, never a
        value captured once at creation — there's no stale-snapshot risk to guard against here."""
        session = await self._get_owned_session(session_id, user_id=user_id)
        session.approval_mode = approval_mode
        session = await self._sessions.update(session)
        return SessionMapper.to_response(session)

    async def select_brand(self, session_id: str, *, user_id: str, brand_id: str) -> SessionResponse:
        """New (2026-10-05, explicit user ask: a brand picker dropdown in the Brand DNA tab) —
        switches this session to one of the user's own already-scraped/saved brands without
        re-crawling. Brand ownership itself is verified by the route handler (`BrandDnaService.
        get_brand`, which already raises Forbidden/NotFound on another user's brand) BEFORE this is
        ever called — this method only applies an already-verified id, same division of
        responsibility as every other owned-session mutation here."""
        session = await self._get_owned_session(session_id, user_id=user_id)
        session.brand_profile_id = brand_id
        session = await self._sessions.update(session)
        
        # Re-derive guardrails immediately so the frontend sees the new brand's DNA
        # rather than falling back to the old one.
        from ....services.knowledge.guardrail_service import GuardrailService
        guardrail_svc = GuardrailService()
        await guardrail_svc.get_or_derive_for_session(session_id)
        
        # Refetch the session so the response includes the newly derived guardrails
        session = await self._sessions.get(session_id)
        return SessionMapper.to_response(session)

    async def update_guardrails_enabled(
        self, session_id: str, *, user_id: str, guardrails_enabled: bool
    ) -> SessionResponse:
        """Per-session on/off toggle (2026-09-25, explicit user ask). Safe to change at any point,
        same reasoning as `update_approval_mode` above — both real enforcement points
        (`_run_turn_inner`'s guardrails-XML injection, `_run_compliance_background`'s compliance
        gate) read the session's CURRENT value fresh on each turn, never a value captured once."""
        session = await self._get_owned_session(session_id, user_id=user_id)
        session.guardrails_enabled = guardrails_enabled
        session = await self._sessions.update(session)
        return SessionMapper.to_response(session)

    async def update_title(self, session_id: str, *, user_id: str, title: str) -> SessionResponse:
        """Rename a workflow (2026-09-30, explicit user ask: "add a delete/edit button on
        workflows") — same ownership-checked shape as `update_approval_mode`/
        `update_guardrails_enabled` above."""
        session = await self._get_owned_session(session_id, user_id=user_id)
        session.title = title
        session = await self._sessions.update(session)
        return SessionMapper.to_response(session)

    async def delete_session(self, session_id: str, *, user_id: str) -> None:
        """Permanently deletes a workflow and everything on it (2026-09-30, explicit user ask).
        Ownership-checked the same way every other session route is — `_get_owned_session` raises
        Forbidden/NotFoundError before any deletion happens, so a user can never delete (or even
        discover the existence of) another user's session by guessing an id. The real cascade
        (canvas elements/versions, chat turns, generation jobs, tool call logs) lives in the
        repository (`PostgresSessionRepository.delete` — Rules.md section 2: only repositories touch
        the database)."""
        await self._get_owned_session(session_id, user_id=user_id)
        await self._sessions.delete(session_id)
        # Drops this session's entries from core/events.py's per-session dicts (_queues,
        # _event_accumulators, _thinking_accumulators, etc.) — without this, that module leaks one
        # entry per distinct session_id ever seen, for the life of the process, since turn-start
        # only ever resets a session's entries, never removes them.
        cleanup_session(session_id)

    async def list_sessions(self, *, user_id: str) -> list[SessionResponse]:
        sessions = await self._sessions.list_for_user(user_id)
        return [SessionMapper.to_response(s) for s in sessions]

    async def list_turns(
        self,
        session_id: str,
        *,
        user_id: str,
        limit: int | None = None,
        before_id: str | None = None,
    ) -> list[ChatTurnResponse]:
        """Real, persisted chat history (2026-09-22) — the actual fix for "the chat forgets
        everything on refresh": every prior turn's real user message, the real accumulated
        "thinking" text streamed live during it, and the real final response, not just the
        session's current status. Ownership-checked the same way every other session route is.

        `limit`/`before_id` (2026-10-05, chat lazy-load) — the chat panel's initial render now
        asks for only the most recent handful of turns instead of the whole history, then pages
        further back on demand (see `ChatTurnRepository.list_for_session`'s own docstring). Both
        default to None/unset so every other caller is unaffected."""
        await self._get_owned_session(session_id, user_id=user_id)
        turns = await self._chat_turns.list_for_session(session_id, limit=limit, before_id=before_id)
        
        referenced_ids = set()
        for t in turns:
            if getattr(t, "referenced_element_ids", None):
                referenced_ids.update(t.referenced_element_ids)
        
        referenced_elements = {}
        if referenced_ids:
            existing_elements = await self._canvas.list_for_session(session_id)
            from ...mappers.canvas_mapper import CanvasMapper
            for e in existing_elements:
                if e.id in referenced_ids:
                    resp = await CanvasMapper.to_response(e)
                    kind = "video" if resp.element_type == "video" else "audio" if resp.element_type == "audio" else "text" if resp.element_type == "text" else "image"
                    url = f"/api/v1/canvas/assets/{resp.storage_ref}" if resp.storage_ref else ""
                    referenced_elements[e.id] = {
                        "id": resp.id,
                        "kind": kind,
                        "url": url,
                        "description": resp.description,
                    }

        return [
            ChatTurnResponse(
                id=t.id, user_text=t.user_text, thinking_text=t.thinking_text,
                assistant_text=t.assistant_text, created_at=t.created_at,
                events=t.events_json or [],
                plan=t.plan_json,
                referenced_elements=[referenced_elements[rid] for rid in getattr(t, "referenced_element_ids", []) or [] if rid in referenced_elements]
            )
            for t in turns
        ]

    async def begin_turn(
        self,
        session_id: str,
        *,
        user_id: str,
        picked_option_id: str | None,
        free_text: str | None,
        referenced_element_ids: list[str] | None = None,
        target_product_id: str | None = None,
        start_new_product: bool = False,
    ) -> SessionResponse:
        """Fire-and-forget variant of post_turn (2026-10-06) — does all validation and preparation
        synchronously, marks status='generating', then dispatches _run_turn as a detached asyncio
        background task and returns immediately.  The HTTP response comes back in milliseconds, so
        Next.js's proxy 30-second timeout can never be reached.  The frontend receives live events
        via SSE and fetches GET /sessions/{id} after the turn_completed event to get the final
        SessionResponse."""
        session = await self._get_owned_session(session_id, user_id=user_id)
        if session.status == "generating" or session_id in _RUNNING_TURNS:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="A generation is still running or cancelling. Please wait a moment and try again.")
        if not picked_option_id and not free_text:
            raise ValidationFailed("Provide either picked_option_id or free_text")

        # Product/Brand crawler turn auto-detection — same fire-and-forget as post_turn.
        if free_text:
            url_match = _URL_RE.search(free_text)
            if url_match:
                url = url_match.group(0).rstrip(").,;\"'")
                crawled_urls = set(session.brief.get("crawled_urls") or [])
                if url not in crawled_urls:
                    from ..crawlers.crawl_runner import run_crawl_and_ingest
                    task = asyncio.create_task(run_crawl_and_ingest(session_id, url))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)

        # Staged edit approval — handled synchronously like post_turn.
        pending_edit_id = session.brief.get("_pending_edit_approval_id")
        if pending_edit_id:
            reply = free_text or (session.brief.get("_last_option_labels", {}) or {}).get(
                picked_option_id, picked_option_id
            )
            if is_approval(reply) or picked_option_id == "approve_edit":
                await self._versioning.approve_pending_edit(pending_edit_id)
                message = "Approved — the staged edit is now the current version."
            elif is_cancel(reply) or picked_option_id == "reject_edit":
                await self._versioning.reject_pending_edit(pending_edit_id)
                message = "Rejected — discarded, nothing changed."
            else:
                session = await self._sessions.update(session)
                return SessionMapper.to_response(session)
            session.brief = {k: v for k, v in session.brief.items() if k != "_pending_edit_approval_id"}
            session.status = "completed"
            session.next_prompt_json = IdeationPrompt(message=message, options=[], allow_free_text=True).model_dump()
            session = await self._sessions.update(session)
            return SessionMapper.to_response(session)

        # Laya fallback approval — handled synchronously like post_turn.
        last_option_labels = session.brief.get("_last_option_labels") or {}
        laya_approve_keys = [k for k in last_option_labels if k.startswith("laya_approve_")]
        if laya_approve_keys:
            reply = free_text or last_option_labels.get(picked_option_id, picked_option_id)
            if picked_option_id == "cancel" or is_cancel(reply):
                session.brief = {k: v for k, v in session.brief.items() if k != "_last_option_labels"}
                session.status = "completed"
                session.next_prompt_json = IdeationPrompt(
                    message="Cancelled — nothing was run.", options=[], allow_free_text=True
                ).model_dump()
                session = await self._sessions.update(session)
                return SessionMapper.to_response(session)
            approved_key = picked_option_id if picked_option_id in laya_approve_keys else (
                laya_approve_keys[0] if is_approval(reply) else None
            )
            if approved_key:
                laya_specialist = approved_key.removeprefix("laya_approve_")
                past_turns = await self._chat_turns.list_for_session(session.id)
                original_message = past_turns[-1].user_text if past_turns else ""
                brief_with_target = dict(session.brief)
                brief_with_target["_laya_approved_specialist"] = laya_specialist
                session.brief = brief_with_target
                # For the laya path, still run synchronously — it's already fast (no LLM, direct dispatch).
                return await self._run_turn(
                    session, user_message=original_message, referenced_element_ids=referenced_element_ids,
                    target_product_id=target_product_id, start_new_product=start_new_product,
                )

        # Resolve yes/no free-text reply to a pending option.
        last_option_labels = session.brief.get("_last_option_labels") or {}
        if not picked_option_id and free_text and last_option_labels:
            reply = free_text.strip()
            if reply not in last_option_labels and reply not in last_option_labels.values():
                resolved_label = _resolve_yes_no_reply(reply, last_option_labels)
                if resolved_label:
                    free_text = resolved_label

        user_message = free_text or (session.brief.get("_last_option_labels", {}) or {}).get(
            picked_option_id, picked_option_id
        )
        if not free_text and (user_message or "").strip().lower() == _BARE_RETRY_LABEL:
            past_turns = await self._chat_turns.list_for_session(session.id)
            real_message, real_refs = _find_real_user_message_and_refs(past_turns)
            if real_message:
                user_message = real_message
            if real_refs and not referenced_element_ids:
                referenced_element_ids = real_refs

        # Mark generating NOW so GET /sessions/{id} immediately reflects it.
        session.status = "generating"
        session.next_prompt_json = None
        if picked_option_id:
            session.brief["_last_picked_option_id"] = picked_option_id
        session = await self._sessions.update(session)

        # Fire-and-forget: dispatch the actual turn in a background task with its own DB session.
        task = asyncio.create_task(_run_turn_bg(
            session_id=session.id,
            user_message=user_message,
            referenced_element_ids=referenced_element_ids,
            target_product_id=target_product_id,
            start_new_product=start_new_product,
        ))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return SessionMapper.to_response(session)

    async def get_session(self, session_id: str, *, user_id: str) -> SessionResponse:
        session = await self._get_owned_session(session_id, user_id=user_id)
        return SessionMapper.to_response(session)

    async def _get_owned_session(self, session_id: str, *, user_id: str) -> SessionModel:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError("Session", session_id)
        # Sessions created before auth existed (or orphaned test data) have `user_id=None` — never
        # treated as "owned by everyone"; only a real matching owner may act on them.
        if session.user_id != user_id:
            raise Forbidden("This workflow belongs to a different user")
        return session

    async def _run_turn(
        self, session: SessionModel, *, user_message: str, referenced_element_ids: list[str] | None = None,
        target_product_id: str | None = None, start_new_product: bool = False,
    ) -> SessionResponse:
        # Sets the ContextVar every nested call (Leads, specialists, tools) reads to emit live
        # events for THIS turn, without session_id being threaded through every function
        # signature in between (core/events.py, same pattern as correlation.py's correlation ID).
        set_current_session(session.id)
        start_new_turn(session.id)
        emit("turn_started", user_message=user_message)
        # A real, persisted "a turn is actually running" marker (2026-09-22) — a real, live-found
        # gap: nothing on the session's own row ever indicated a turn was in flight server-side, so
        # a page reload mid-generation had no way to tell "still working" apart from whatever
        # status was left over from the LAST completed turn — Node Mode/chat just looked frozen or
        # blank, even though the graph below keeps running to completion regardless of the client
        # (uvicorn does not cancel this coroutine on a client disconnect). Persisted immediately,
        # BEFORE the real — possibly slow — graph execution, so a concurrent GET /{session_id} from
        # a reloaded tab can actually see it and poll until it resolves, instead of guessing.
        session.status = "generating"
        session.next_prompt_json = None
        session = await self._sessions.update(session)
        
        task = asyncio.current_task()
        if task:
            _RUNNING_TURNS[session.id] = task
            
        try:
            return await self._run_turn_inner(
                session, user_message=user_message, referenced_element_ids=referenced_element_ids,
                target_product_id=target_product_id, start_new_product=start_new_product,
            )
        except Exception as exc:
            # A real, live-found regression in the "generating" marker just added above
            # (2026-09-22): it was written unconditionally, but nothing guaranteed it would ever
            # be overwritten by a TERMINAL status if the turn crashed with a genuine unhandled
            # exception (as opposed to a `SpecialistFailed` a graph node already catches and turns
            # into a normal error `result` — those still complete normally). Caught live: a bare
            # `NameError` in `graph.py` (a missing `import json`, since fixed) left a real user's
            # session stuck at `"generating"` forever — permanently, since nothing else ever wrote
            # to that row again. The NEW frontend poll (`ChatPanel.tsx`) only stops polling once
            # status moves past `"generating"`, so this bug would have made THAT poll loop forever
            # too, on any future crash, not just this one. Never let that repeat: any unhandled
            # exception here now resolves the session to a real, honest terminal state before
            # re-raising (preserving the existing "genuine 500, not a fabricated success" behavior
            # for the CURRENT request) — so a reloaded tab's poll always terminates either way.
            log.error("turn_crashed", extra={"_extra_error": str(exc)})
            session.status = "error"
            session.next_prompt_json = IdeationPrompt(
                message=f"Something went wrong while generating: {exc}",
                options=[], allow_free_text=True,
            ).model_dump()
            await self._sessions.update(session)
            emit("turn_completed", status=session.status)
            raise
        except asyncio.CancelledError:
            log.info("turn_cancelled", extra={"_extra_session": session.id})
            session.status = "error"
            session.brief["_error"] = "Turn cancelled by user."
            session.next_prompt_json = IdeationPrompt(
                message="Generation cancelled by user.",
                options=[], allow_free_text=True,
            ).model_dump()
            await self._sessions.update(session)
            emit("turn_completed", status=session.status)
            raise
        finally:
            _RUNNING_TURNS.pop(session.id, None)
            await mark_turn_done(session.id)

    async def _run_turn_inner(
        self, session: SessionModel, *, user_message: str, referenced_element_ids: list[str] | None = None,
        target_product_id: str | None = None, start_new_product: bool = False,
    ) -> SessionResponse:
        # The direct_fix route needs something to act on. Prefer the one the user explicitly picked
        # in the UI ("reference an element in chat") — a stale or unknown id just falls back rather
        # than erroring the whole turn. Real, live-found bug (user report): silently defaulting to
        # "the most recently produced element" whenever NOTHING was explicitly referenced caused a
        # referenced-but-wrong product to be silently edited (cross-product confusion) once more than
        # one element existed on the canvas. Only auto-pick a target when there's no real ambiguity —
        # exactly one existing element — otherwise leave it unset so Ideation's own
        # "MULTIPLE ELEMENTS ON CANVAS" prompt (`ideation_service.py`) asks which one, instead of
        # guessing.
        existing_elements = await self._canvas.list_for_session(session.id)
        latest_element = existing_elements[-1] if len(existing_elements) == 1 else None
        referenced_elements = []
        if referenced_element_ids:
            for rid in referenced_element_ids:
                ref = next((e for e in existing_elements if e.id == rid), None)
                if ref is not None:
                    referenced_elements.append(ref)
            if referenced_elements:
                latest_element = referenced_elements[-1]

        # Canvas Grouping (2026-09-25, revised same day per explicit user correction: a workflow IS
        # one campaign — grouping is by real Product DNA instead). Resolved here in two passes:
        # this first pass covers an explicit pick or inheriting the referenced parent's own
        # product; if NEITHER applies, `resolved_product_id` stays None for now and gets one more
        # real chance later in this same turn — after the graph runs, from whatever product this
        # turn's own chat message gets auto-detected as being about (the `product_facts` block
        # below, already running every turn for Product DNA — this just also lets its result drive
        # grouping when nothing more specific was already known). Handed to every
        # `_add_new_element` call this turn makes (main result + all byproducts).
        # `parent_element_id` is deliberately NOT a separate request field — derived from
        # `referenced_element_ids[0]` (the first explicitly-referenced element), one source of
        # truth instead of two that could disagree.
        parent_element = referenced_elements[0] if referenced_elements else None
        resolved_parent_element_id = parent_element.id if parent_element else None
        resolved_product_id: str | None = None
        resolved_product_name: str | None = None
        
        import logging
        log = logging.getLogger(__name__)
        log.info(f"[GROUPING] target_product_id: {target_product_id}, referenced_element_ids: {referenced_element_ids}")
        log.info(f"[GROUPING] parent_element: {parent_element.id if parent_element else None}, start_new_product: {start_new_product}")
        
        if target_product_id:
            # An explicit pick from the session's own known products — the real name is looked up
            # server-side, never trusted from the client.
            from ...repositories.postgres.postgres_product_repository import (
                PostgresProductRepository,
            )
            async with async_session_factory() as db:
                picked_product = await PostgresProductRepository(db).get(target_product_id)
            if picked_product:
                resolved_product_id = picked_product.id
                resolved_product_name = picked_product.name
            log.info(f"[GROUPING] resolved from target_product_id: {resolved_product_id}")
        elif parent_element and parent_element.product_id and not start_new_product:
            # No explicit target — inherit the referenced element's own product, so a follow-up
            # generation from an existing product's asset stays grouped with it by default.
            # `start_new_product` (Phase 1, 2026-09-28) is the user's explicit "+ Start new
            # product" choice — it defeats this inheritance on purpose, even though a parent WAS
            # referenced, so the new element starts fresh (chat-detection/unassigned) instead of
            # silently landing back in the product it was branched FROM. `resolved_parent_element_id`
            # above is untouched either way — the lineage badge ("🔗 Based on...") should still
            # show what this was derived from, even when it's deliberately grouped differently;
            # `target_product_id: null`/omitted with no explicit flag still means "no opinion,
            # infer as before" — unchanged, only this NEW explicit flag skips inheritance.
            resolved_product_id = parent_element.product_id
            resolved_product_name = parent_element.product_name
            log.info(f"[GROUPING] resolved from parent_element: {resolved_product_id}")

        # Genuinely nothing resolved yet — may still be filled in after the graph runs (see
        # `product_facts` below); if that doesn't resolve it either, lands in the frontend's flat
        # "Unassigned" bucket (`CanvasEngine.tsx`), never a fabricated grouping.

        brief_for_graph = dict(session.brief)
        brief_for_graph["user_id"] = session.user_id
        # Scratch, read-only — threaded through so the `recall` tool (`services/tools/recall.py`,
        # 2026-10-05) can scope its lookup to this session; `runner.py`'s shared tool-context
        # builder reads it the same way it already reads `user_id`/`product_id`.
        brief_for_graph["session_id"] = session.id
        brief_for_graph["brand_profile_id"] = session.brand_profile_id
        # Tiered conversation memory (2026-10-05, explicit user design — Ledger/Window/Digests/
        # Recall/caching) — replaces the old flat, UNCAPPED "last 6 turns" slice (no token budget,
        # no masking of old long text) plus an UNCONDITIONAL semantic-memory call on every single
        # turn regardless of whether anything needed recalling. `brief.idea` staying lossy —
        # resummarized every turn, eroding real figures like "$1500/12% off" — is the exact bug
        # Digests fix: a span of turns is summarized ONCE, from the real turns it covers, not
        # re-derived from an already-lossy prior summary. See `conversation_memory.py`'s own module
        # docstring for the full design; `available_context_block` (`leads/base.py`) is what
        # actually renders the Ledger/Digests into a specialist's context — this block only
        # computes and persists the data.
        from ..knowledge.conversation_memory import build_ledger, build_window, update_digests

        all_turns = await self._chat_turns.list_for_session(session.id)
        window, dropped = build_window(all_turns)
        if window:
            brief_for_graph["_recent_chat_history"] = window

        existing_digests = session.brief.get("memory_digests") or []
        updated_digests = await update_digests(existing_digests, dropped)
        ledger = build_ledger(existing_elements, current_focus_id=latest_element.id if latest_element else None)
        # Explicit dict reassignment, not in-place mutation (`session.brief["x"] = y`) — a plain
        # JSON column (not `MutableDict.as_mutable`), so SQLAlchemy's dirty-tracking doesn't
        # reliably pick up an in-place change (the exact class of bug already documented/fixed
        # elsewhere in this file for other session.brief writes).
        session.brief = {**session.brief, "memory_digests": updated_digests, "ledger": ledger}
        brief_for_graph["memory_digests"] = updated_digests
        brief_for_graph["ledger"] = ledger

        # `recall` (`services/tools/recall.py`) replaces the old unconditional semantic-memory
        # call above — a real on-demand tool a specialist calls only when the Ledger/Window/
        # Digests it already has don't answer the question, not something paid for every turn.
        from ..knowledge.chat_memory_service import ChatMemoryService
        chat_memory = ChatMemoryService()
        # Read-only, sourced from the session's own column, never persisted back into brief JSON
        # (Memory.md, Phase 4: "approve" mode's per-stage pipeline gates).
        brief_for_graph["approval_mode"] = session.approval_mode
        # Scratch, turn-input-only (2026-09-26, Fix 3 of the image/video quality investigation):
        # the raw current message, threaded through `brief` so `runner.py`'s single shared
        # tool-execution choke point can deterministically enforce a detected aspect ratio
        # (`infer_aspect_ratio_from_text`) regardless of which node/route ends up calling the
        # image/video generation tool — `brief["idea"]` is not always this turn's own message (it
        # can be stale/summarized), so this is threaded separately rather than reusing that field.
        brief_for_graph["_current_turn_message"] = user_message
        
        from ...core.deliverables import detect_deliverable, detect_deliverable_keys
        # Real, live-found bug (2026-10-06): a message naming TWO different deliverables (e.g.
        # "make a youtube thumbnail... and also an instagram 9:16 post") used to resolve to a
        # single global `brief["deliverable"]` (whichever pattern matched first), which
        # `runner.py`'s aspect-ratio enforcement then force-applied to EVERY image-generating tool
        # call this turn — both steps silently collapsed onto one wrong aspect ratio. When 2+
        # distinct deliverables are named, deliberately leave `brief["deliverable"]` unset so each
        # dynamic-plan step falls back to its own per-step instruction-derived aspect ratio
        # (illustrator.md's own rule already handles "set aspect_ratio to match the actual
        # requested format") instead of one shared, wrong value.
        if len(detect_deliverable_keys(user_message)) <= 1:
            detected_spec = detect_deliverable(user_message)
            if detected_spec:
                brief_for_graph["deliverable"] = detected_spec.key
            
        # Fallback fields for backwards compatibility with parts of graph that expect latest_element
        if latest_element:
            brief_for_graph["latest_element_id"] = latest_element.id
            brief_for_graph["latest_element_storage_ref"] = latest_element.storage_ref
            brief_for_graph["latest_element_type"] = latest_element.element_type

        # Multi-element context
        ref_context = []
        # Fix 7 (2026-09-26, real gap: "it keeps taking the same image no matter what I say" —
        # confirmed root cause was zero semantic reasoning here, a pure boolean branch that
        # blindly defaulted to `existing_elements[-1]`, the single most-recently-created element
        # in the ENTIRE session, regardless of what the message actually says). When the user gave
        # an explicit reference, that's authoritative — no ambiguity, no reasoning needed (cost
        # discipline: skip when deterministic). Otherwise, when there's genuinely more than one
        # real candidate to choose between, a small CAPPED set of the most recent ones is handed
        # to the orchestrator's own classification call (already running once per turn regardless
        # — no new LLM round-trip) as real candidates, flagged via `_element_disambiguation_needed`
        # so its prompt knows to pick which ONE (if any) the message is actually about — same or
        # different from any previous one — rather than a hardcoded recency guess. See
        # `orchestrator.py`'s `route()`, which narrows `referenced_elements_context` down to the
        # resolved single element (or none) before this turn's generation ever runs.
        _MAX_DISAMBIGUATION_CANDIDATES = 3
        if referenced_elements:
            elements_to_contextualize = referenced_elements
        elif len(existing_elements) > 1:
            elements_to_contextualize = existing_elements[-_MAX_DISAMBIGUATION_CANDIDATES:]
            brief_for_graph["_element_disambiguation_needed"] = True
        else:
            elements_to_contextualize = [latest_element] if latest_element else []

        for el in elements_to_contextualize:
            meta = el.metadata_json or {}
            description = (
                meta.get("image_prompt") or meta.get("frame_prompt") or meta.get("motion_prompt")
                or meta.get("voiceover_line") or meta.get("text")
            )
            if description is None:
                if el.element_type == "audio":
                    description = await self._describe_uploaded_audio(el.storage_ref)
                elif el.element_type == "image":
                    description = await self._describe_uploaded_image(el.storage_ref, el.product_name)
            
            ref_context.append({
                "id": el.id,
                "storage_ref": el.storage_ref,
                "element_type": el.element_type,
                "description": description or NO_DESCRIPTION_SENTINEL
            })
            
            # for backwards compatibility for older prompts relying on this
            if el.id == (latest_element.id if latest_element else None):
                brief_for_graph["latest_element_description"] = description
                
        brief_for_graph["referenced_elements_context"] = ref_context

        # Real, live-found ordering bug (2026-09-26, root-caused by tracing an actual bad
        # generation end-to-end): this chat-driven product detection/switch used to run AFTER
        # `graph.ainvoke` below, which meant a turn about a NEW product still generated using the
        # PREVIOUS turn's product photo/guardrails as its image-to-image anchor — `qwen-image-3`'s
        # i2i mode then structurally locked onto that stale, irrelevant photo (confirmed: a Red
        # Bull collab request generated as a re-skinned iPhone product photo from an earlier,
        # unrelated campaign in the same session). Moved here, BEFORE guardrail derivation and the
        # `product_photo_storage_ref` injection below, so both correctly reflect the product THIS
        # turn is actually about — not one turn late. This is a reordering, not new logic; every
        # piece was already computed, just too late to matter for the turn that needed it.
        stripped_message = user_message.strip()
        if stripped_message and len(stripped_message) >= 12 and not is_approval(stripped_message) and not is_cancel(stripped_message):
            from ...core.exceptions import SpecialistFailed
            from ...repositories.postgres.postgres_product_repository import (
                PostgresProductRepository,
            )
            from ..knowledge.product_dna_service import ProductDnaService

            try:
                async with async_session_factory() as db:
                    product_repo = PostgresProductRepository(db)
                    product_dna_svc = ProductDnaService(product_repo)
                    linked_ids = list(session.brief.get("product_profile_ids") or [])
                    existing_products = [
                        p for pid in linked_ids if (p := await product_repo.get(pid)) is not None
                    ]
                    product = await product_dna_svc.upsert_product_from_chat(
                        user_id=session.user_id,
                        existing_products=existing_products,
                        raw_text=stripped_message,
                    )
                if product is not None:
                    if product.id not in linked_ids:
                        linked_ids.append(product.id)
                    session.brief = {**session.brief, "product_profile_ids": linked_ids}
                    session.product_profile_id = product.id
                    # Canvas Grouping's second resolution pass — only fills in what the EARLY pass
                    # (explicit pick / inherited parent, above) left unresolved. An explicit pick
                    # or a real parent's own product always takes precedence over a fresh
                    # chat-detected one, so refining an existing product's DNA mid-turn never
                    # silently re-groups an element the user already anchored elsewhere.
                    if resolved_product_id is None:
                        resolved_product_id = product.id
                        resolved_product_name = product.name
            except SpecialistFailed as exc:
                log.warning(
                    "product_dna_chat_upsert_failed",
                    extra={"_extra_session_id": session.id, "_extra_error": exc.message},
                )

        from ..knowledge.guardrail_service import GuardrailService
        guardrail_svc = GuardrailService(self._sessions)

        # Per the reference design (guardrails.py::resolve): guardrails are derived ONCE from
        # brand/product at session creation and live in the session brief forever.
        # They are NEVER re-inferred from chat messages — doing so causes mid-session rules like
        # "do not show prices" to block the agent on a "try again" turn.
        # The ONLY way to add new rules is via the explicit user action: clicking Add in the
        # Guardrails UI, which calls add_rule_from_user_context() via POST /guardrails.
        # (Derived AFTER the chat-driven product detection above, so a product switched THIS turn
        # is reflected in the rules the specialists actually see during generation, not the
        # previous turn's product's rules.)
        guardrail_set = await guardrail_svc.get_or_derive_for_session(session.id)
        brief_for_graph["guardrails"] = guardrail_set.model_dump()
        # Real, live-found bug (2026-09-30): the brand's real uploaded logo never reached
        # generation at all — only prose brand facts did, via brand_kit_lookup — so illustrator
        # could only ever hallucinate a logo from a text description. Injected into the brief the
        # same way product_photo_storage_ref already is, just below.
        brand_logo_storage_ref = await guardrail_svc.get_brand_logo_storage_ref(session)
        if brand_logo_storage_ref:
            brief_for_graph["brand_logo_storage_ref"] = brand_logo_storage_ref

        # Inject product photo storage ref into the brief so base_image_generator can
        # auto-use it as image-to-image reference when no explicit reference_storage_ref is given.
        # Real, live-found cross-product contamination bug (2026-10-03): `session.product_profile_id`
        # is the LAST product the session's own chat-detection touched — in a session with multiple
        # products, a turn referencing an S26 element while the session is "on" the Nothing Phone
        # product injected the Nothing Phone's photo as `product_photo_storage_ref`, causing
        # Environment Designer to generate a scene WITH the wrong phone. Fix: when a referenced
        # element belongs to a specific product (`resolved_product_id` already set above from the
        # parent's own product_id), prefer THAT product's photo first. Fall back to the
        # session-level product only when the reference has no product assignment.
        photo_product_id = resolved_product_id or (session.product_profile_id if not start_new_product else None)
        if photo_product_id:
            async with async_session_factory() as db:
                from ...repositories.postgres.postgres_product_repository import (
                    PostgresProductRepository,
                )
                product_repo = PostgresProductRepository(db)
                product = await product_repo.get(photo_product_id)
                if product:
                    if product.photo_storage_ref:
                        brief_for_graph["product_photo_storage_ref"] = product.photo_storage_ref
                    elif "product_photo_storage_ref" in brief_for_graph:
                        # Clear stale persisted value for old sessions
                        del brief_for_graph["product_photo_storage_ref"]
                    
                    # Last-resort grouping fallback (Fix 5, 2026-09-26): only fills in what the
                    # explicit pick / inherited parent / chat-detection above left unresolved.
                    if resolved_product_id is None and not start_new_product:
                        resolved_product_id = product.id
                        resolved_product_name = product.name
        elif "product_photo_storage_ref" in brief_for_graph:
            del brief_for_graph["product_photo_storage_ref"]

        # `runner.py`'s tool-context choke point only has access to `brief`, not to this
        # function's local `resolved_product_id` — thread it through so `product_lookup`'s own
        # scoping (services/tools/product_lookup.py) can reach it. Set AFTER the last-resort
        # grouping fallback above, which can still resolve this from `None`.
        brief_for_graph["resolved_product_id"] = resolved_product_id

        from ...core.events import set_current_guardrails_xml
        from ...core.guardrails import scope_to_product
        # Per-session toggle (2026-09-25) — when off, every specialist's system prompt this turn
        # gets an empty guardrails block instead of the real rules (runner.py reads this same
        # ContextVar). `brief_for_graph["guardrails"]` above is left untouched either way so the
        # Guardrails UI can still show/edit the underlying rules while they're switched off.
        # `scope_to_product` (2026-09-30, real bug: a session with 3 linked products handed every
        # specialist 3 conflicting "must show this exact product" rules at once) — only the XML
        # ContextVar every specialist actually reads gets scoped to THIS turn's resolved product;
        # the full, persisted, UI-editable set above is untouched.
        turn_guardrails = scope_to_product(guardrail_set, resolved_product_id)
        set_current_guardrails_xml(turn_guardrails.render() if session.guardrails_enabled else "")

        # Turn-level specialist-call budget (Part 8, fidelity audit 2026-10-05) — a circuit breaker
        # across every entry point below (direct resume, full_video resume, dynamic-executor
        # resume, or a fresh `graph.ainvoke`), generous enough for the real pipeline depth after
        # this session's hop reductions (happy path ~2-6 calls; a full multi-shot video with
        # retries is the worst real case, comfortably under this) but real enough to stop a
        # pathological loop from silently burning spend across resumed turns.
        from ..specialists.runner import start_turn_budget
        start_turn_budget(40)

        # Resuming a paused clarification question (2026-09-30, explicit user ask: "multi step
        # plans can also stop and ask, doesn't have to be restart") — invokes the SAME paused node
        # directly, bypassing the compiled graph's ideation->orchestrator entry routing entirely
        # for this one turn, so already-completed steps' real results are reused rather than the
        # whole plan restarting from step 1. `session.brief["paused_plan"]` was set the turn the
        # question was asked (below, mirroring `state.get("paused_plan")`) and is only ever cleared
        # once this resume actually completes or fails outright (see below) — a fresh pause on a
        # NEW question overwrites it instead, so a chain of clarifications resumes correctly too.
        paused_plan = session.brief.get("paused_plan")
        if paused_plan:
            picked_id = brief_for_graph.pop("_last_picked_option_id", None)
            answer_text = f"{user_message} (Option ID: {picked_id})" if picked_id else user_message
            resume_brief = {**brief_for_graph, "clarification_answer": answer_text}
            resume_state: GraphState = {
                "session_id": session.id, "user_id": session.user_id,
                "user_message": user_message, "brief": resume_brief,
            }
            if paused_plan.get("route") == "plan_approval":
                # Real, live-found gap closed (2026-10-07, explicit user ask: "after creating a
                # plan there should be hitl, be it campaign generation or any generation") —
                # `orchestrator.py`'s `route()` now always pauses here before any specialist runs
                # (see its own comment for why). Three outcomes, same shape as every other
                # approve/cancel/revise gate in this file (e.g. the staged-edit branch right above):
                pending_route = paused_plan.get("pending_route")
                original_message = paused_plan.get("original_message", "")
                if is_approval(user_message):
                    # Re-enter the graph directly at the REAL execution node, using the plan
                    # already shown to and approved by the user — never re-invokes
                    # `orchestrator.route()`, so the approved plan can never be silently
                    # re-classified into something different.
                    resume_state["user_message"] = original_message
                    resume_brief.pop("clarification_answer", None)
                    resume_state["brief"] = resume_brief
                    
                    # Re-emit structural events so Node Mode redraws the flow leading up to this resume point
                    # rather than appearing cleared out.
                    emit("ideation_started")
                    emit("ideation_completed", ready=True)
                    if paused_plan.get("plan_preview"):
                        emit("plan_proposed", route=pending_route, plan=paused_plan["plan_preview"])
                    emit("route_decided", route=pending_route, target_specialist=paused_plan.get("target_specialist"))

                    if pending_route == "direct_fix":
                        resume_state["target_specialist"] = paused_plan.get("target_specialist")
                        result_state = await _direct_fix_node(resume_state)
                    elif pending_route == "full_image":
                        from .graph import _visual_design_lead_node
                        result_state = await _visual_design_lead_node(resume_state)
                    elif pending_route == "full_video":
                        from .graph import _motion_lead_node
                        result_state = await _motion_lead_node(resume_state)
                    elif pending_route == "full_audio":
                        from .graph import _full_audio_node
                        result_state = await _full_audio_node(resume_state)
                    else:
                        resume_state["dynamic_plan"] = paused_plan.get("dynamic_plan")
                        result_state = await _dynamic_executor_node(resume_state)
                elif is_cancel(user_message):
                    session.brief = {k: v for k, v in session.brief.items() if k != "paused_plan"}
                    result_state = {
                        "brief": resume_brief,
                        "result": {
                            "message": "Cancelled — the proposed plan was discarded and nothing was "
                                       "generated. Send a new idea whenever you're ready.",
                        },
                    }
                else:
                    # Anything else is revision feedback, not a clear approve/cancel — re-run the
                    # real orchestrator with the revision appended, so a NEW plan is proposed and
                    # the SAME gate fires again; a revision is never silently applied as if it were
                    # the original, already-rejected plan.
                    combined_message = f"{original_message}\nUser revision feedback: {user_message}".strip()
                    resume_brief["idea"] = combined_message
                    resume_state["user_message"] = combined_message
                    resume_state.pop("target_specialist", None)
                    resume_state.pop("dynamic_plan", None)
                    graph = get_graph()
                    result_state = await graph.ainvoke(
                        {
                            "session_id": session.id, "user_id": session.user_id,
                            "user_message": combined_message, "brief": resume_brief,
                        }
                    )
            elif paused_plan.get("route") == "direct_fix":
                emit("ideation_started")
                emit("ideation_completed", ready=True)
                emit("route_decided", route="direct_fix", target_specialist=paused_plan.get("specialist_name"))

                resume_state["target_specialist"] = paused_plan.get("specialist_name")
                resume_brief["_resume_direct_fix_context"] = paused_plan.get("context")
                result_state = await _direct_fix_node(resume_state)
            elif paused_plan.get("route") == "full_video":
                # The video pipeline doesn't have true step-by-step resume, but we can restart it fresh
                # with the combined context of the original idea and the user's new answer, ensuring it
                # doesn't get misrouted by the orchestrator.
                #
                # Real, flagged risk (2026-10-06, Ctruh Agent Engine cross-check): a pause/failure
                # inside the video pipeline can occur AFTER real, PAID generation already succeeded
                # for one or more earlier shots (scene_builder/camera_director calls, each a real
                # Replicate charge) — this restart has no per-shot memory of that, so a resume can
                # re-generate and re-bill shots that already completed successfully before the
                # pause. A full fix needs per-shot resume state threaded through `run_motion_lead`
                # (out of scope for this pass); logged loudly here so a double-charge is visible and
                # auditable in the run log, not silent.
                log.warning(
                    "full_video_resume_restarts_from_scratch",
                    extra={
                        "_extra_session_id": session.id,
                        "_extra_note": (
                            "resuming via _motion_lead_node restart — any shot that already "
                            "completed a real paid generation before this pause/failure will be "
                            "regenerated and re-billed; no per-shot resume exists yet"
                        ),
                    },
                )
                emit("ideation_started")
                emit("ideation_completed", ready=True)
                emit("route_decided", route="full_video", target_specialist=None)

                original_message = paused_plan.get("original_message", "")
                combined_idea = f"{original_message}\nUser clarification answer: {user_message}".strip()
                resume_brief["idea"] = combined_idea
                resume_state["user_message"] = combined_idea

                from .graph import _motion_lead_node
                result_state = await _motion_lead_node(resume_state)
            else:
                emit("ideation_started")
                emit("ideation_completed", ready=True)
                emit("route_decided", route="dynamic_plan", target_specialist=None)

                resume_state["dynamic_plan"] = paused_plan.get("plan")
                resume_brief["_resume_next_step_index"] = paused_plan.get("next_step_index")
                resume_brief["_resume_completed_results"] = paused_plan.get("completed_results")
                result_state = await _dynamic_executor_node(resume_state)
        else:
            from ..ideation.requirements_check import run_requirements_check
            req_prompt = run_requirements_check(user_message, brief_for_graph, existing_elements, referenced_elements)

            if req_prompt:
                result_state = {
                    "brief": brief_for_graph,
                    "result": {
                        "message": req_prompt.message,
                        "options": [opt.model_dump() for opt in req_prompt.options],
                    }
                }
            else:
                graph = get_graph()
                result_state = await graph.ainvoke(
                    {
                        "session_id": session.id,
                        "user_id": session.user_id,
                        "user_message": user_message,
                        "brief": brief_for_graph,
                    }
                )

        # Only the fields ideation/orchestrator actually mutate belong in the persisted brief —
        # the latest-element scratch fields above are graph-input-only, not part of the session's
        # own accumulated state.
        returned_brief = result_state.get("brief") or session.brief
        _scratch_keys = (
            "latest_element_id", "latest_element_storage_ref", "latest_element_type",
            "latest_element_description", "approval_mode", "_recent_chat_history", "_retrieved_memory",
            "_current_turn_message", "_element_disambiguation_needed",
            # This turn's own mood/style announcement (ideation_service.py) — read out via
            # result.metadata below, same as partial_generation_note; never persisted, or it would
            # keep re-announcing an old choice on later, unrelated turns.
            "style_note",
            # A one-shot resume flag (post_turn's Laya-fallback-approval handling) — consumed by
            # `orchestrator.py`'s `route()` for THIS turn only; left in `session.brief` it would
            # force-route every later, unrelated turn in the session to the same specialist forever.
            "_laya_approved_specialist",
            # Resume-only scratch (2026-09-30) — graph-input-only for a paused-plan resume, same
            # reasoning as the other scratch keys above; never part of the session's own
            # accumulated state (`resolved_product_id`/`brand_logo_storage_ref` are recomputed
            # fresh every turn anyway, same as `approval_mode`).
            "resolved_product_id", "brand_logo_storage_ref", "product_photo_storage_ref", "clarification_answer",
            "_resume_direct_fix_context", "_resume_next_step_index", "_resume_completed_results",
        )
        session.brief = {k: v for k, v in returned_brief.items() if k not in _scratch_keys}

        # `paused_plan` is a top-level GraphState field (not nested in `brief`), set by
        # `_direct_fix_node`/`_dynamic_executor_node` when a step raises
        # `SpecialistNeedsClarification` — persisted here so the SAME plan can resume next turn
        # instead of restarting. Always overwritten/cleared based on THIS turn's real outcome: a
        # fresh pause replaces any prior one (a chain of clarifications resumes correctly), and a
        # turn that completes/fails outright (no new pause) clears it rather than leaving a stale
        # resume point a later, unrelated turn would incorrectly try to resume into.
        new_paused_plan = result_state.get("paused_plan")
        if new_paused_plan:
            session.brief = {**session.brief, "paused_plan": new_paused_plan}
        elif "paused_plan" in session.brief:
            session.brief = {k: v for k, v in session.brief.items() if k != "paused_plan"}
        
        # Append any new guardrails extracted during the turn (e.g. from Ideation, 2026-09-24) —
        # brand/custom rules only now; product facts are handled separately below, unconditionally,
        # not gated behind this (see that block's own docstring for the real, live-found reason).
        new_guardrails = result_state.get("new_guardrails") or []
        other_new_rules = [g for g in new_guardrails if isinstance(g, dict) and g.get("source") != "product"]

        # (Chat-driven product detection/upsert used to run here, after the graph — moved earlier
        # in this function, before guardrail derivation and the graph call, so a product switched
        # THIS turn is reflected in what the graph actually sees instead of one turn late. See the
        # "Real, live-found ordering bug (2026-09-26...)" comment above `guardrail_svc = ...`.)

        if other_new_rules:
            from ...core.guardrails import GuardrailRule
            for g in other_new_rules:
                if "id" not in g:
                    g["id"] = f"rule_{uuid.uuid4().hex[:8]}"
                # default to 'custom' if source not recognized
                if g.get("source") not in ("brand", "product", "project", "custom"):
                    g["source"] = "custom"
                guardrail_set.rules.append(GuardrailRule(**g))
            # `update_guardrails` already indexes internally (`guardrail_service.py`) — a second
            # call here was pure redundant re-indexing of the same data, removed alongside making
            # `index_guardrails` properly async/awaited (2026-09-25).
            await guardrail_svc.update_guardrails(session.id, guardrail_set.model_dump())
            session.brief["guardrails"] = guardrail_set.model_dump()

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
            element_id: str | None = None
            # Real, live-found bug (2026-09-22, independent review): "approve" mode's
            # `apply_or_stage` only sets `pending_storage_ref` — the element's real `storage_ref`
            # is untouched until the user explicitly approves it (canvas/routes.py's
            # /approve-edit). The turn used to unconditionally report "completed" and schedule QA
            # regardless, so a staged-but-not-yet-approved chat edit got a real compliance verdict
            # written against the OLD, unedited asset while the client was told the turn finished.
            edit_staged_not_applied = False
            if result.get("update_existing_element_id"):
                # A direct_fix that adjusted the existing element in place — recorded as a real
                # new version (not a raw mutation) via the same CanvasVersioningService every
                # other edit path (regenerate/comment/direct-edit) already uses, so the existing
                # /undo, /redo, /versions endpoints work for chat-driven edits too, and "approve"
                # mode correctly stages this instead of always applying immediately.
                target = await self._canvas.get_element(result["update_existing_element_id"])
                if target is not None:
                    target = await self._versioning.apply_or_stage(
                        target,
                        storage_ref=result["storage_ref"],
                        metadata={**target.metadata_json, **result.get("metadata", {})},
                        action="direct_fix",
                        approval_mode=session.approval_mode,
                        # A direct_fix can genuinely change what KIND of asset this element is
                        # (graph.py's `_ELEMENT_TYPE_BY_TOOL`, e.g. video_stitcher turning a still
                        # image into a video) — passed through so the version history records the
                        # real type at each version, not just at the element's latest one.
                        element_type=result.get("element_type"),
                    )
                    element_id = target.id
                    edit_staged_not_applied = target.pending_storage_ref is not None
                else:
                    element_id = (await self._add_new_element(
                        session.id, result, product_id=resolved_product_id,
                        product_name=resolved_product_name, parent_element_id=resolved_parent_element_id,
                    )).id
            else:
                element_id = (await self._add_new_element(
                    session.id, result, product_id=resolved_product_id,
                    product_name=resolved_product_name, parent_element_id=resolved_parent_element_id,
                )).id
                # Real intermediate artifacts this turn genuinely produced beyond the main result
                # (2026-09-22) — a scene's starting still, a raw pre-stitch clip, a standalone
                # voiceover track, an overlaid still. Only for a brand-new element, never the
                # `update_existing_element_id` (direct_fix) branch above — a direct_fix's result
                # never populates `extra_elements` (only Motion Lead does today), so this is a
                # no-op there regardless, but the placement itself matches the reference
                # architecture's intent: these are byproducts of a fresh GENERATION, not of an
                # in-place EDIT to something that already exists.
                for extra in result.get("extra_elements") or []:
                    # An honest "not checked" (the same disclosed meaning `compliance_status:
                    # "disabled"` already carries when the QA gate is turned off entirely) — the
                    # compliance gate only ever runs against the turn's MAIN result, never these
                    # byproducts, so leaving the model's own "running" default here would show a
                    # QA check that will never actually complete.
                    # Canvas Grouping: byproducts share the SAME product/parent as the main result
                    # above — a Motion Lead voiceover track belongs with the same product as the
                    # video it's part of, never falls out of it silently.
                    await self._add_new_element(
                        session.id, extra, compliance_status="disabled",
                        product_id=resolved_product_id, product_name=resolved_product_name,
                        parent_element_id=resolved_parent_element_id,
                    )

            if edit_staged_not_applied:
                # Honest status — the edit is real and staged, but not yet the current version;
                # QA against the still-unchanged asset would tell the user nothing true about what
                # they're about to approve.
                session.status = "awaiting_approval"
                # Real, live-found gap (2026-09-22): `options=[]` here meant this gate — unlike
                # every OTHER approval gate in this app (the video pipeline's narrative/scene/motion
                # stages) — had no chat-actionable way to resolve it at all; a user had to go find
                # the canvas's separate Elements drawer instead, even though this message reads
                # like it expects a reply. `_pending_edit_approval_id` (a normal persisted `brief`
                # field, not a same-turn scratch key — must survive to the NEXT turn) is read by
                # `post_turn`'s own short-circuit below, which resolves a pick/reply here without
                # ever routing through the ideation/orchestrator graph — approving/rejecting a
                # staged edit isn't a new creative request.
                session.brief = {**session.brief, "_pending_edit_approval_id": element_id}
                prompt = IdeationPrompt(
                    message="A direct edit is staged for this element — approve or reject it below "
                    "before it becomes the current version.",
                    options=[
                        {"id": "approve_edit", "label": "Approve", "description": "Apply this staged edit"},
                        {"id": "reject_edit", "label": "Reject", "description": "Discard this staged edit"},
                    ],
                    allow_free_text=True,
                )
            else:
                session.status = "completed"
                # Real QA, kicked off automatically right after generation — a live-found gap
                # (2026-09-21): `run_compliance_gate` already existed and worked, but nothing in the
                # real user-facing flow ever called it. NOT awaited here on purpose — per the user's
                # own explicit ask, the element should appear on canvas immediately (already
                # `compliance_status: "running"` by the model's own default) while QA runs
                # afterward, rather than the whole turn waiting on it and only showing the element
                # once QA has already finished.
                #
                # `COMPLIANCE_QA_ENABLED=false` (config.py) skips this entirely — a real extra round
                # of LLM/vision calls per generation, worth turning off for fast local iteration. The
                # element is marked "disabled", not "passed" — an honest "not checked", never a
                # fabricated verdict.
                if settings.compliance_qa_enabled:
                    self._schedule_compliance_check(element_id)
                else:
                    element = await self._canvas.get_element(element_id)
                    if element is not None:
                        element.compliance_status = "disabled"
                    await self._canvas.update_element(element)

                # A real, honest disclosure (2026-09-22, a bug caught via live testing under real
                # provider instability, not just reviewed): multi-generation degrades gracefully
                # when one variant fails (`graph.py`'s `_run_multi_generation`) rather than failing
                # the whole turn — correct, but it used to do so SILENTLY, reporting "completed"
                # with the exact same message a full success gets even when e.g. only 1 of 2
                # requested images actually got made (Rules.md: no fabricated success). Surfaced
                # as a real `next_prompt` even though status is "completed" — the frontend's own
                # `describeResponse` already checks `next_prompt` before falling back to the
                # generic "Generated" line, so this note is what actually shows instead.
                partial_note = (result.get("metadata") or {}).get("partial_generation_note")
                if partial_note:
                    prompt = IdeationPrompt(message=partial_note, options=[], allow_free_text=True)
                else:
                    # The mood/style direction Ideation auto-chose for this turn, if any (point 3 of
                    # ideation_service.py's _SYSTEM_PROMPT) — a real, disclosed creative decision,
                    # not a silent guess, surfaced the same way partial_generation_note is (a real
                    # `next_prompt` even though status is "completed"). Lower priority than a partial
                    # -failure disclosure, which is more urgent when both are somehow true at once.
                    style_note = (result.get("metadata") or {}).get("style_note")
                    if style_note:
                        prompt = IdeationPrompt(message=style_note, options=[], allow_free_text=True)
                    else:
                        # Partner-style narration (Part 7, fidelity audit 2026-10-05): a plain
                        # successful generation used to surface nothing beyond the frontend's own
                        # flat fallback ("Generated — check the canvas") — no record of what was
                        # actually decided. Build a short, real summary from whatever creative-
                        # decision fields this result's metadata actually has (varies by Lead) —
                        # same "real next_prompt even on completed" pattern as the notes above,
                        # kept to one short line, never a long narrative.
                        meta = result.get("metadata") or {}
                        decision_bits = [
                            b for b in (meta.get("aesthetic_direction"), meta.get("palette_direction"))
                            if b
                        ]
                        if meta.get("shot_count_rendered", 1) > 1:
                            decision_bits.append(f"{meta['shot_count_rendered']} shots, stitched into one video")
                        if decision_bits:
                            prompt = IdeationPrompt(
                                message=" · ".join(decision_bits), options=[], allow_free_text=True
                            )
        elif result:
            # A placeholder or error result — surface the message, nothing to persist yet.
            session.status = "error" if result_state.get("error") else "pending"
            prompt = IdeationPrompt(message=result.get("message", ""), options=[], allow_free_text=True)

        session.next_prompt_json = prompt.model_dump() if prompt else None
        session = await self._sessions.update(session)

        # Real, persisted chat history (2026-09-22) — one row per turn, capturing what a live
        # client would have shown: the real user message, the real accumulated "thinking" text
        # streamed during this turn (core/events.py), and the real final response text. Mirrors
        # the frontend's own `describeResponse()` fallback for a real "completed" turn with no
        # `next_prompt` (a fresh generation) — kept in sync deliberately, so restored history reads
        # exactly like the live turn did, not a differently-worded reconstruction.
        assistant_text = (
            prompt.message if prompt
            else "Generated — check the canvas behind this chat." if session.status == "completed"
            else f"Status: {session.status}"
        )
        await self._chat_turns.add(ChatTurnModel(
            id=uuid.uuid4().hex,
            session_id=session.id,
            user_text=user_message,
            thinking_text=get_current_turn_thinking(session.id),
            assistant_text=assistant_text,
            referenced_element_ids=referenced_element_ids,
            # Real, persisted Node Mode run history for this turn (2026-09-22) — see
            # `models/chat_turn.py`'s own docstring for why this exists.
            events_json=get_current_turn_events(session.id),
            # The plan-preview bubble's own data (2026-10-06) — see `models/chat_turn.py`'s own
            # docstring. `result_state` is whatever `orchestrator.route()`/the resume path set on
            # `state["plan_preview"]`; absent for any turn that never routed (an error before
            # routing), persisting `None` there is correct.
            plan_json=result_state.get("plan_preview"),
        ))
        
        # Ingest into the semantic ChatMemoryService so it can be recalled in future turns
        await chat_memory.add_turn(
            session_id=session.id,
            turn_id=uuid.uuid4().hex,
            user_text=user_message,
            assistant_text=assistant_text
        )

        emit("turn_completed", status=session.status)
        return SessionMapper.to_response(session)

    async def _add_new_element(
        self, session_id: str, result: dict, *, compliance_status: str | None = None,
        product_id: str | None = None, product_name: str | None = None,
        parent_element_id: str | None = None,
    ) -> CanvasElementModel:
        element = CanvasElementModel(
            id=uuid.uuid4().hex,
            session_id=session_id,
            element_type=result.get("element_type", "image"),
            produced_by_specialist=result.get("produced_by_specialist", "unknown"),
            # `.get(...)`, not `result["storage_ref"]` (2026-09-22) — a real "text" extra_element
            # (a shot list, a scene description) has no binary asset at all; the text itself IS
            # the content, stored in `metadata["text"]` instead. The MAIN turn result always still
            # has a real storage_ref (checked by the caller before this is ever invoked for it).
            storage_ref=result.get("storage_ref"),
            metadata_json=result.get("metadata", {}),
            # Canvas Grouping (2026-09-25, revised: a workflow IS one campaign, so grouping is by
            # real Product DNA instead) — every caller of this method (the turn's main result AND
            # every `extra_elements` byproduct: Motion Lead's voiceover/overlay tracks, Visual
            # Design Lead's alternates, the dynamic plan's own extras) passes the SAME resolved
            # product for this turn, computed once in `_run_turn_inner` — so a video pipeline's
            # byproducts never silently fall out of the product their main result belongs to.
            product_id=product_id,
            product_name=product_name,
            parent_element_id=parent_element_id,
        )
        if compliance_status is not None:
            element.compliance_status = compliance_status
        return await self._canvas.add_element(element)

    @staticmethod
    async def _describe_uploaded_audio(storage_ref: str) -> str | None:
        """Real, local speech-to-text + pace + acoustic mood for an audio element this app did NOT
        itself generate (2026-09-22) — see `providers/audio/local_whisper.py` for why this is more
        than just a transcript. An honest `None` (never a fabricated description) if the asset is
        missing or transcription genuinely fails — the caller already handles `None` the same as
        "nothing recorded", the same honest-gap behavior this had before this feature existed."""
        from ...core.local_storage import load_asset
        from ...providers.audio.local_whisper import get_transcription_provider

        loaded = await load_asset(storage_ref)
        if loaded is None:
            return None
        audio_bytes, mime_type = loaded
        try:
            result = await get_transcription_provider().transcribe(audio_bytes=audio_bytes, mime_type=mime_type)
        except Exception as exc:
            log.warning("uploaded_audio_transcription_failed", extra={"_extra_error": str(exc)})
            return None
        if not result.text:
            return None
        parts = [f'Spoken content: "{result.text}"']
        if result.pace_label:
            parts.append(f"pace: {result.pace_label} (~{result.pace_wpm} words/min)")
        if result.mood:
            parts.append(f"acoustic tone: {result.mood}")
        return " — ".join(parts)

    @staticmethod
    async def _describe_uploaded_image(storage_ref: str, product_name: str | None = None) -> str | None:
        """Real, local vision inference for an image element this app did NOT itself generate, or
        that lost its prompt. Fails safely to None if the asset is missing or the provider fails.

        `product_name` (2026-09-28, real, live-found bug: a "Nothing Phone (4b)" back-panel photo
        — camera module + Glyph LED array, no screen/front face — got captioned as a "smartphone
        case" by a small, free-tier vision model with a fully generic prompt and zero knowledge of
        what product was expected, which then made `shot_planner` wrongly refuse a real request
        against this session's own guardrails. Product Grouping already tags a canvas element with
        its real product name (`CanvasElementModel.product_name`), so the caller passes it straight
        through here — grounding the prompt costs nothing and gives the model real context instead
        of guessing blind. `None` (an untagged upload) keeps today's fully generic prompt."""
        from ...core.local_storage import asset_mime_type, load_asset, public_url
        from ...providers.llm.vision import complete_with_vision

        # Real, live-found latency win (2026-09-29): skip downloading the bytes entirely when
        # this asset already has a direct Cloudinary url — a plain local metadata read
        # (`asset_mime_type`) is enough to confirm the ref is real and get its mime type.
        image_url = await public_url(storage_ref)
        image_bytes: bytes | None = None
        if image_url is not None:
            mime_type = await asset_mime_type(storage_ref)
            if mime_type is None:
                return None
        else:
            loaded = await load_asset(storage_ref)
            if loaded is None:
                return None
            image_bytes, mime_type = loaded
        # Real, live-found performance bug (2026-10-05, fidelity audit): this result becomes
        # `verified_description` (`canvas_mapper.py`'s own priority-1 description field) — asking
        # for "detail" at the shared 700-token default produced several-hundred-word essays per
        # image, sent in full on every canvas list fetch. Asking for a short, grounded summary
        # instead (not every other `complete_with_vision` caller — compliance checkers genuinely
        # need the longer, more exhaustive default; only this one call site, whose output is a
        # stored, repeatedly-served description, not a one-off QA judgment).
        question = (
            "In 2-3 sentences, describe this image's main subject, objects, colors, and setting — "
            "concise, not exhaustive."
        )
        if product_name:
            question = (
                f"This image is expected to show the product '{product_name}'. In 2-3 sentences, "
                f"describe what you actually see — concise, not exhaustive — and state explicitly "
                f"whether it does or doesn't look like '{product_name}'."
            )
        try:
            result = await complete_with_vision(
                image_bytes=image_bytes,
                mime_type=mime_type,
                image_url=image_url,
                system="You are a meticulous visual analyzer for a marketing team.",
                question=question,
                max_tokens=200,
            )
            return result.text
        except Exception as exc:
            log.warning("uploaded_image_vision_failed", extra={"_extra_error": str(exc)})
            return None

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
        canvas = PostgresCanvasRepository(db)
        if not settings.compliance_qa_enabled:
            await canvas.update_compliance_status(element_id, "disabled")
            return

        # Per-session toggle (2026-09-25) — mirrors the global `compliance_qa_enabled` kill switch
        # just above, scoped to one session instead of every session. Looked up fresh here (not
        # threaded in from the caller) since this runs as a detached background task, potentially
        # well after the request that created the element has already returned.
        element = await canvas.get_element(element_id)
        if element is not None:
            from ...repositories.postgres.postgres_session_repository import (
                PostgresSessionRepository,
            )
            owning_session = await PostgresSessionRepository(db).get(element.session_id)
            if owning_session is not None and not owning_session.guardrails_enabled:
                await canvas.update_compliance_status(element_id, "disabled")
                return

        try:
            gate_result = await run_compliance_gate(canvas=canvas, element_id=element_id)
            status = "passed" if gate_result.get("overall_passed") else "failed"
        except Exception:
            status = "error"

        await canvas.update_compliance_status(element_id, status)
