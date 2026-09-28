"""
Ties a session's persistence (via the SessionRepository/CanvasRepository protocols) to running the
LangGraph graph. This is business logic — it belongs in services/, not in a route handler
(Rules.md section 2).
"""
from __future__ import annotations

import asyncio
import uuid

from ...core.approval import is_approval, is_cancel
from ...core.config import settings
from ...core.element_descriptions import NO_DESCRIPTION_SENTINEL
from ...core.events import (
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
from ...repositories.sqlite.sqlite_canvas_repository import SqliteCanvasRepository
from ...schemas.sessions.responses import (
    ChatTurnResponse,
    IdeationOption,
    IdeationPrompt,
    SessionResponse,
)
from ..canvas.versioning_service import CanvasVersioningService
from ..compliance.compliance_gate import run_compliance_gate
from .graph import get_graph

# Real, live-found asyncio gotcha (2026-09-21): a bare `asyncio.create_task(...)` with no
# reference held anywhere can be garbage-collected mid-run, silently killing the background QA
# check before it ever persists a result — a task's only strong reference by default is this
# variable holding it. Kept at module scope (not per-instance) since SessionService itself is
# constructed fresh per request; the task must outlive that.
_background_tasks: set[asyncio.Task] = set()
_RUNNING_TURNS: dict[str, asyncio.Task] = {}
log = get_logger(__name__)

def cancel_running_turn(session_id: str) -> bool:
    """Cancels the currently running _run_turn task for the given session_id, if any."""
    task = _RUNNING_TURNS.get(session_id)
    if task and not task.done():
        task.cancel()
        return True
    return False



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
        self, *, user_id: str, approval_mode: str = "auto", title: str | None = None
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

    async def list_sessions(self, *, user_id: str) -> list[SessionResponse]:
        sessions = await self._sessions.list_for_user(user_id)
        return [SessionMapper.to_response(s) for s in sessions]

    async def list_turns(self, session_id: str, *, user_id: str) -> list[ChatTurnResponse]:
        """Real, persisted chat history (2026-09-22) — the actual fix for "the chat forgets
        everything on refresh": every prior turn's real user message, the real accumulated
        "thinking" text streamed live during it, and the real final response, not just the
        session's current status. Ownership-checked the same way every other session route is."""
        await self._get_owned_session(session_id, user_id=user_id)
        turns = await self._chat_turns.list_for_session(session_id)
        
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
                    resp = CanvasMapper.to_response(e)
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
                referenced_elements=[referenced_elements[rid] for rid in getattr(t, "referenced_element_ids", []) or [] if rid in referenced_elements]
            )
            for t in turns
        ]

    async def post_turn(
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
        session = await self._get_owned_session(session_id, user_id=user_id)
        if not picked_option_id and not free_text:
            raise ValidationFailed("Provide either picked_option_id or free_text")

        # A real, chat-actionable resolution for a staged per-element direct edit (2026-09-22, see
        # the matching comment where `_pending_edit_approval_id` is set in `_run_turn_inner`) —
        # handled here, BEFORE the graph ever runs, since approving/rejecting a staged edit is not
        # a new creative request for the ideation/orchestrator pipeline to interpret. Reuses the
        # exact same `CanvasVersioningService` methods the canvas UI's own Approve/Reject buttons
        # already call (`api/v1/canvas/routes.py`'s `/approve-edit`/`/reject-edit`), so a chat reply
        # and a canvas click do exactly the same real thing.
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
                # An unclear reply never means yes (core/approval.py's own rule, already applied
                # to the video pipeline's motion-spend gate) — leave the gate open rather than
                # guessing at a real, currently-pending change.
                session = await self._sessions.update(session)
                return SessionMapper.to_response(session)
            session.brief = {k: v for k, v in session.brief.items() if k != "_pending_edit_approval_id"}
            session.status = "completed"
            session.next_prompt_json = IdeationPrompt(message=message, options=[], allow_free_text=True).model_dump()
            session = await self._sessions.update(session)
            return SessionMapper.to_response(session)

        # A real, live-found bug (2026-09-23, per an explicit user report: "Cancel is also
        # creating llm task??"): the Laya-fallback "approval_required" prompt (orchestrator.py,
        # when both real LLM gateways are down but Laya can still suggest a specialist) built its
        # options as `laya_approve_{specialist}`/`cancel`, but NEITHER was ever specially handled
        # here — both silently fell through to the generic path below, which treats a picked
        # option as just its label TEXT and re-runs the full ideation/orchestrator pipeline with
        # it as a brand-new user message. So "No, cancel" didn't cancel anything: it sent the
        # literal words "No, cancel" back through real LLM classification (hence a fresh "Thinking
        # about the brief…" the user never asked for), and "Yes, use X" didn't actually invoke X
        # either — it sent "Yes, use X" back through the SAME classifier that had just degraded in
        # the first place, likely to misroute or degrade again. Handled explicitly here, the same
        # "resolve a real pending gate before the graph ever re-runs" shape as the
        # `_pending_edit_approval_id` block just above.
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
                # The real request text got lost the moment classification degraded — it was never
                # persisted anywhere except the chat history itself, so recover it from the most
                # recent real (non-empty) turn rather than re-asking the user to repeat themselves.
                past_turns = await self._chat_turns.list_for_session(session.id)
                original_message = past_turns[-1].user_text if past_turns else ""
                brief_with_target = dict(session.brief)
                # Consumed once by `orchestrator.py`'s `route()` (checked before real
                # classification, same shape as its existing `video_stage` resume-check) — skips
                # re-classifying with the same degraded LLM path and goes straight to `direct_fix`
                # with the specialist the user just explicitly approved.
                brief_with_target["_laya_approved_specialist"] = laya_specialist
                session.brief = brief_with_target
                return await self._run_turn(
                    session, user_message=original_message, referenced_element_ids=referenced_element_ids,
                    target_product_id=target_product_id, start_new_product=start_new_product,
                )
            # Neither a clear approve nor a clear cancel (e.g. the user typed something else
            # entirely instead of picking either option) — treat it as a genuinely new message,
            # same as the generic fallthrough below.

        # A real, live-found bug (2026-09-22, traced from a real session's `brief.idea` getting
        # corrupted to the literal string "approve"): a free-text reply to an open ideation
        # clarifying question (`_last_option_labels` set, real pickable options shown) that doesn't
        # literally match any option's id/label — e.g. typing "approve" instead of clicking a
        # button labeled "Yes, add $1500 RS as the price tag." — fell straight through as raw,
        # context-free `user_message` text. `ideation_service.py`'s own clarification-accumulation
        # fix then concatenated it onto the pending question text into one confusing blob, which is
        # what the orchestrator's classifier actually saw as "the user's latest message" — a real,
        # traced cause of at least one live misroute. Resolved deterministically here, same
        # "a plain check beats trusting an LLM every time" reasoning as `_is_bare_greeting`/
        # `_check_price_stated` (`ideation_service.py`) and the `_pending_edit_approval_id` gate
        # just above: if the reply is a clear yes/no-shaped word AND one of the pending options
        # itself reads as the yes/no answer (its own label starts with "yes"/"no"), resolve to that
        # option's real label — exactly as if it had been clicked — instead of passing an
        # unresolved bare word forward. Genuinely ambiguous option sets (neither option reads as
        # yes/no — most subject/style disambiguation choices) are left untouched; no unsafe guess.
        last_option_labels = session.brief.get("_last_option_labels") or {}
        if not picked_option_id and free_text and last_option_labels:
            reply = free_text.strip()
            if reply not in last_option_labels and reply not in last_option_labels.values():
                resolved_label = _resolve_yes_no_reply(reply, last_option_labels)
                if resolved_label:
                    free_text = resolved_label

        # A card pick is just shorthand for its label — the graph's ideation node only deals in
        # plain text either way (Architecture.md section 1d: a pick is a shortcut, not a
        # different code path).
        user_message = free_text or (session.brief.get("_last_option_labels", {}) or {}).get(
            picked_option_id, picked_option_id
        )
        # See `_find_real_user_message_and_refs`'s own comment above: a bare "retry" is a
        # continuation of the real prior request, not a new one — recovered from real chat history
        # so it's what actually reaches classification/ideation/persisted history/semantic memory
        # this turn, instead of the content-free label "Try again" itself. The reference is
        # recovered the same way, and only used to FILL IN a gap — an explicit reference the caller
        # already sent (e.g. the user picked a different element for this retry) always wins.
        if not free_text and (user_message or "").strip().lower() == _BARE_RETRY_LABEL:
            past_turns = await self._chat_turns.list_for_session(session.id)
            real_message, real_refs = _find_real_user_message_and_refs(past_turns)
            if real_message:
                user_message = real_message
            if real_refs and not referenced_element_ids:
                referenced_element_ids = real_refs
        return await self._run_turn(
            session, user_message=user_message, referenced_element_ids=referenced_element_ids,
            target_product_id=target_product_id, start_new_product=start_new_product,
        )

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
        # The direct_fix route needs something to act on — the most recently produced element by
        # default (Memory.md, Phase 3 conformance audit), or the one the user explicitly picked in
        # the UI ("reference an element in chat") when they named one that's still real — a stale
        # or unknown id just falls back to the default rather than erroring the whole turn.
        existing_elements = await self._canvas.list_for_session(session.id)
        latest_element = existing_elements[-1] if existing_elements else None
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
        if target_product_id:
            # An explicit pick from the session's own known products — the real name is looked up
            # server-side, never trusted from the client.
            from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
            async with async_session_factory() as db:
                picked_product = await SqliteProductRepository(db).get(target_product_id)
            if picked_product:
                resolved_product_id = picked_product.id
                resolved_product_name = picked_product.name
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
        # Genuinely nothing resolved yet — may still be filled in after the graph runs (see
        # `product_facts` below); if that doesn't resolve it either, lands in the frontend's flat
        # "Unassigned" bucket (`CanvasEngine.tsx`), never a fabricated grouping.

        brief_for_graph = dict(session.brief)
        brief_for_graph["user_id"] = session.user_id
        # Real conversation history (2026-09-22, per an explicit user ask: "make sure the llm has
        # chat history context cache, so it can work in a session") — a real, live-found gap: this
        # app already persists every real turn verbatim (`ChatTurnModel`, `self._chat_turns`), but
        # NOTHING ever fed it back into an actual LLM call — every ideation/orchestrator call was a
        # single stateless message built from `brief.idea`, a summary the model itself re-writes
        # every turn. `brief.idea` staying lossy was a deliberate, documented tradeoff (the
        # "numeric erosion" bug — resummarizing repeatedly lost real figures like "$1500/12% off")
        # but the fix for THAT bug never replaced real memory with something better, it just
        # accepted losing it. Real, VERBATIM past turns (never re-summarized, so they can't erode
        # the same way) are read here and handed to `ideation_service.py`/`orchestrator.py` as a
        # real scratch field — capped to the most recent 6 turns to bound token growth on a
        # long-lived session (`test_set`'s real 26-element session made this a genuine concern, not
        # a hypothetical one). Read-only and never persisted into `session.brief` (recomputed fresh
        # from the real `chat_turns` table every turn, same treatment as `approval_mode` above).
        recent_turns = await self._chat_turns.list_for_session(session.id)
        if recent_turns:
            brief_for_graph["_recent_chat_history"] = [
                {"user": t.user_text, "assistant": t.assistant_text or ""}
                for t in recent_turns[-6:]
            ]
        
        # Real, semantic LLM context caching (2026-09-23) — retrieves older, relevant turns from LlamaIndex
        # so the LLM doesn't lose long-term memory beyond the strict 6-turn rolling window above.
        from ..knowledge.chat_memory_service import ChatMemoryService
        chat_memory = ChatMemoryService()
        if user_message:
            retrieved_memory = await chat_memory.get_relevant_history(session.id, user_message)
            if retrieved_memory:
                brief_for_graph["_retrieved_memory"] = retrieved_memory
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
                    description = await self._describe_uploaded_image(el.storage_ref)
            
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
            from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
            from ..knowledge.product_dna_service import ProductDnaService

            try:
                async with async_session_factory() as db:
                    product_repo = SqliteProductRepository(db)
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

        # Inject product photo storage ref into the brief so base_image_generator can
        # auto-use it as image-to-image reference when no explicit reference_storage_ref is given.
        # (Reads `session.product_profile_id` AFTER the chat-driven update above, so this is the
        # CURRENT turn's product's photo, not a stale one left over from an earlier campaign.)
        if session.product_profile_id and not start_new_product and (
            not brief_for_graph.get("product_photo_storage_ref") or resolved_product_id is None
        ):
            # `not start_new_product` guards this WHOLE block (2026-09-28, Phase 1): both effects
            # below — auto-grounding generation on the session's current product photo, AND the
            # session-level grouping fallback — are exactly the kind of unrequested inheritance
            # `start_new_product` exists to defeat. Without this guard, "+ Start new product"
            # would still silently land back in the session's existing product here, one level
            # down from the parent-inheritance check above.
            async with async_session_factory() as db:
                from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
                product_repo = SqliteProductRepository(db)
                product = await product_repo.get(session.product_profile_id)
                if product:
                    if product.photo_storage_ref and not brief_for_graph.get("product_photo_storage_ref"):
                        brief_for_graph["product_photo_storage_ref"] = product.photo_storage_ref
                    # Fix 5 (2026-09-26): last-resort grouping fallback — an explicit pick, an
                    # inherited parent's product, or this turn's own chat-detected product (all
                    # above) all still take precedence; only when NONE of those resolved anything
                    # does a plain creative-direction turn now still land in the session's current
                    # product instead of `None`/"Unassigned" (verified: 5/61 elements resolved a
                    # product before this fix — this closes that gap for any session that has ever
                    # had one attached, reusing the same DB fetch above, no extra query).
                    if resolved_product_id is None:
                        resolved_product_id = product.id
                        resolved_product_name = product.name

        from ...core.events import set_current_guardrails_xml
        # Per-session toggle (2026-09-25) — when off, every specialist's system prompt this turn
        # gets an empty guardrails block instead of the real rules (runner.py reads this same
        # ContextVar). `brief_for_graph["guardrails"]` above is left untouched either way so the
        # Guardrails UI can still show/edit the underlying rules while they're switched off.
        set_current_guardrails_xml(guardrail_set.render() if session.guardrails_enabled else "")

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
        )
        session.brief = {k: v for k, v in returned_brief.items() if k not in _scratch_keys}
        
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

        loaded = load_asset(storage_ref)
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
    async def _describe_uploaded_image(storage_ref: str) -> str | None:
        """Real, local vision inference for an image element this app did NOT itself generate, or 
        that lost its prompt. Fails safely to None if the asset is missing or the provider fails."""
        from ...core.local_storage import load_asset
        from ...providers.llm.vision import complete_with_vision

        loaded = load_asset(storage_ref)
        if loaded is None:
            return None
        image_bytes, mime_type = loaded
        try:
            result = await complete_with_vision(
                image_bytes=image_bytes,
                mime_type=mime_type,
                system="You are a meticulous visual analyzer for a marketing team.",
                question="Describe this image in detail, focusing on the main visual subjects, objects, colors, and setting."
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
        canvas = SqliteCanvasRepository(db)
        if not settings.compliance_qa_enabled:
            await canvas.update_compliance_status(element_id, "disabled")
            return

        # Per-session toggle (2026-09-25) — mirrors the global `compliance_qa_enabled` kill switch
        # just above, scoped to one session instead of every session. Looked up fresh here (not
        # threaded in from the caller) since this runs as a detached background task, potentially
        # well after the request that created the element has already returned.
        element = await canvas.get_element(element_id)
        if element is not None:
            from ...repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
            owning_session = await SqliteSessionRepository(db).get(element.session_id)
            if owning_session is not None and not owning_session.guardrails_enabled:
                await canvas.update_compliance_status(element_id, "disabled")
                return

        try:
            gate_result = await run_compliance_gate(canvas=canvas, element_id=element_id)
            status = "passed" if gate_result.get("overall_passed") else "failed"
        except Exception:
            status = "error"

        await canvas.update_compliance_status(element_id, status)
