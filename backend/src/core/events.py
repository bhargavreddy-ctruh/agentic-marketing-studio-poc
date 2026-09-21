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


def set_current_session(session_id: str) -> None:
    _current_session_id.set(session_id)


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
    try:
        _get_queue(session_id).put_nowait({"type": event_type, **data})
    except asyncio.QueueFull:
        log.warning("event_queue_full", extra={"_extra_session_id": session_id, "_extra_event": event_type})


def start_new_turn(session_id: str) -> None:
    """
    Call once at the very start of a turn, before emitting anything. Discards any stale,
    never-consumed events (and a stale _DONE sentinel) left over from an earlier turn nobody
    was listening to. Real bug found live (Memory.md, Phase 4): without this, a listener
    connecting during turn 2 would receive turn 1's entire backlog first — including its old
    _DONE marker — and the stream would terminate right there, before turn 2's actual events
    ever arrived, since a single shared queue has no notion of "which turn" an event belongs to.
    """
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
