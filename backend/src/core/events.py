"""
Live event emission for SSE narration — Architecture.md's "watch generation happen live"
requirement (Phase 4), porting the existing agentic_flow codebase's `emit()` pattern.

A session's current ID is carried via a ContextVar — the same pattern `correlation.py` already
uses for correlation IDs — so a specialist several calls deep inside a Lead can emit a real event
without `session_id` being threaded through every function signature in between.

An in-memory per-session `asyncio.Queue` is the event bus — fine for a single-process POC
(Architecture.md's portability note: swapping to a real pub-sub for a multi-process deployment
later is a provider-style swap, not a rewrite of every `emit()` call site).

Additive, not a replacement: the existing synchronous `POST /sessions` / `POST /turns` contract is
unchanged and still returns the full final result — a client that also opens the SSE connection
(`GET /sessions/{id}/events`) before or during that call sees the same turn's real progress live,
ending with a `turn_completed` event. Nothing about the existing, already-tested request/response
behavior changes.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from .middleware.logging import get_logger

log = get_logger(__name__)

_current_session_id: ContextVar[str | None] = ContextVar("current_session_id", default=None)
_queues: dict[str, asyncio.Queue] = {}

_DONE = object()  # sentinel: "no more events for this session's current turn"

# Real, persisted "thinking" (2026-09-22) — the actual raw streamed text a model produces
# (`llm_delta` events) was always emitted live for the SSE narration and then genuinely discarded
# the instant a turn finished (`ChatPanel.tsx`'s old `withNarration` cleared it in a `finally`
# block) — a real, disclosed gap: it never survived a page refresh, and disappeared from the chat
# entirely once a turn completed, unlike the reference product's own persistent, collapsible
# "Analyzed your request" block. Accumulated per-session via the same ContextVar pattern
# `_current_session_id` already uses, so any specialist/node several calls deep can append to it
# without `session_id` being threaded through every function signature.
_thinking_accumulators: dict[str, list[str]] = {}
# Tracks which real "run" (see `_thinking_run_seq` below) the last-appended `llm_delta` belonged
# to, per session — real, live-found bug (2026-09-22, a user directly seeing the result): a single
# turn genuinely involves several separate LLM calls (ideation, the orchestrator, each specialist a
# Lead runs), and every one of their raw streamed chunks landed in the SAME flat
# `_thinking_accumulators` list with ZERO separation between them — one call's tail-end JSON
# fragment ran directly into the next call's opening brace with no whitespace, producing genuinely
# unreadable text like `"needs_edit": false}{"route": "direct_fix"` in the live "thinking" view. A
# real header + blank line is inserted whenever the run changes (see `emit()` below).
_thinking_last_run_seq: dict[str, int] = {}
# A real, live-found gap in the FIRST version of the fix above, caught immediately after shipping
# (still 2026-09-22): comparing only the node NAME correctly separates DIFFERENT specialists, but a
# specialist that's reviewed and retried (`run_specialist_with_review` — the exact same
# "overlay_artist" name, genuinely two separate completions back to back) still ran together with
# zero separation. Bumped on every real `*_started` event (`specialist_started`/`ideation_started`/
# `lead_started`/etc — a generic suffix check, not a hardcoded list, so it never needs updating when
# a new kind of "started" event is added) — a monotonic sequence number is a real, distinct-per-run
# signal a bare node name can't provide on its own.
_thinking_run_seq: dict[str, int] = {}

# Real, persisted node/tool run history (2026-09-22) — the same real gap as `_thinking_accumulators`
# above, one layer up: every raw event (`turn_started`, `lead_started`, `specialist:*` calls,
# `tool:*` calls, `route_decided`, etc.) was always emitted live for Node Mode's SSE stream and then
# genuinely discarded the instant a turn finished — a real, live-found user report: a page refresh
# mid-generation (or even well after) left Node Mode either empty or permanently stuck showing a
# spinner for steps that had actually already finished, because it is pure client-side `useState`
# with nothing server-side to rebuild it from. Accumulated the same way `_thinking_accumulators` is,
# so `session_service.py` can persist the REAL, COMPLETE run history per turn, and a later refresh
# can ask for it again instead of only ever seeing whatever arrived on the one live SSE connection
# that happened to be open at the time.
_event_accumulators: dict[str, list[dict]] = {}


def set_current_session(session_id: str) -> None:
    _current_session_id.set(session_id)


_current_guardrails_xml: ContextVar[str] = ContextVar("current_guardrails_xml", default="")

def set_current_guardrails_xml(xml: str) -> None:
    _current_guardrails_xml.set(xml)

def get_current_guardrails_xml() -> str:
    return _current_guardrails_xml.get()


def _get_queue(session_id: str) -> asyncio.Queue:
    if session_id not in _queues:
        _queues[session_id] = asyncio.Queue()
    return _queues[session_id]


def emit(event_type: str, **data: Any) -> None:
    """Emits a live event for the CURRENT session (per the ContextVar set at turn start). A
    no-op if no turn is active. Never blocks and never raises — live narration is a real feature,
    but never something that should crash an actual generation step if nobody's listening yet."""
    session_id = _current_session_id.get()
    if session_id is None:
        return
    if event_type.endswith("_started"):
        _thinking_run_seq[session_id] = _thinking_run_seq.get(session_id, 0) + 1
    if event_type == "llm_delta":
        text = data.get("text")
        if text:
            node = data.get("node")
            chunks = _thinking_accumulators.setdefault(session_id, [])
            current_seq = _thinking_run_seq.get(session_id, 0)
            if chunks and _thinking_last_run_seq.get(session_id) != current_seq:
                chunks.append(f"\n\n— {node or 'next step'} —\n")
            _thinking_last_run_seq[session_id] = current_seq
            chunks.append(str(text))
    event = {"type": event_type, **data}
    # `llm_delta` deliberately excluded here — hundreds of tiny per-character/per-token events per
    # turn would bloat the persisted history for no real benefit (the accumulated TEXT is already
    # persisted separately via `_thinking_accumulators`/`thinking_text`); Node Mode's own real value
    # is the node/tool call structure, not a replay of raw token streaming.
    if event_type != "llm_delta":
        _event_accumulators.setdefault(session_id, []).append(event)
    try:
        _get_queue(session_id).put_nowait(event)
    except asyncio.QueueFull:
        log.warning("event_queue_full", extra={"_extra_session_id": session_id, "_extra_event": event_type})


def get_current_turn_thinking(session_id: str) -> str | None:
    """The real, accumulated raw model text streamed during the CURRENT turn so far — read once,
    after the graph finishes, by `session_service.py` to persist it as this turn's real
    `thinking_text`. Returns None (not "") when nothing was ever accumulated — an honest "no
    thinking captured," e.g. `STREAM_LLM_THINKING_ENABLED=false`, not an empty string implying one
    genuinely empty delta arrived."""
    chunks = _thinking_accumulators.get(session_id)
    return "".join(chunks) if chunks else None


def get_current_turn_events(session_id: str) -> list[dict]:
    """The real, complete, ordered event history for the CURRENT turn so far — read once, after the
    graph finishes, by `session_service.py` to persist it as this turn's real run history. Returns
    an empty list (never None) when nothing was accumulated — an honest "no events captured" that
    the caller can store as-is with no special-casing."""
    return list(_event_accumulators.get(session_id, []))


def start_new_turn(session_id: str) -> None:
    """
    Call once at the very start of a turn, before emitting anything. Discards any stale,
    never-consumed events (and a stale _DONE sentinel) left over from an earlier turn nobody
    was listening to. Real bug found live (Memory.md, Phase 4): without this, a listener
    connecting during turn 2 would receive turn 1's entire backlog first — including its old
    _DONE marker — and the stream would terminate right there, before turn 2's actual events
    ever arrived, since a single shared queue has no notion of "which turn" an event belongs to.

    Also resets this session's thinking accumulator — each turn's persisted `thinking_text` must
    be THIS turn's own reasoning, never a prior turn's leftover text.
    """
    _thinking_accumulators[session_id] = []
    _thinking_last_run_seq[session_id] = 0
    _thinking_run_seq[session_id] = 0
    _event_accumulators[session_id] = []
    queue = _get_queue(session_id)
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def mark_turn_done(session_id: str) -> None:
    await _get_queue(session_id).put(_DONE)


async def stream_events(session_id: str):
    """An async generator yielding events for one session until `mark_turn_done()` fires for it.
    Used by the SSE route — a fresh call starts listening from whatever arrives next, so opening
    it right before/during the POST that runs a turn is how a client watches it live."""
    queue = _get_queue(session_id)
    while True:
        event = await queue.get()
        if event is _DONE:
            break
        yield event
