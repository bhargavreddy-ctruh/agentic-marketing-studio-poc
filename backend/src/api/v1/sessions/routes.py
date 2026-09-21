"""
HTTP only — validate, call the service, return (Rules.md section 2).

The SSE route (`GET /{session_id}/events`) is additive, not a replacement (core/events.py) — it
meaningfully applies to `POST /{session_id}/turns` (a client already knows the session_id and can
open the event stream just before/during that call to watch the turn happen live). It does NOT
apply to the very first `POST /` (`start_session`): the session_id isn't known until that call
returns, so genuinely live-watching the FIRST turn isn't possible without a bigger restructure
(e.g. having the client generate the session id) — an honest scope boundary, not an oversight.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from ....core.events import stream_events
from ....schemas.sessions.requests import PostTurnRequest, StartSessionRequest
from ....schemas.sessions.responses import SessionResponse
from ...dependencies import SessionServiceDep

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
async def start_session(body: StartSessionRequest, svc: SessionServiceDep) -> SessionResponse:
    return await svc.start_session(body.initial_message, approval_mode=body.approval_mode)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, svc: SessionServiceDep) -> SessionResponse:
    return await svc.get_session(session_id)


@router.post("/{session_id}/turns", response_model=SessionResponse)
async def post_turn(session_id: str, body: PostTurnRequest, svc: SessionServiceDep) -> SessionResponse:
    return await svc.post_turn(
        session_id,
        picked_option_id=body.picked_option_id,
        free_text=body.free_text,
        referenced_element_id=body.referenced_element_id,
    )


@router.get("/{session_id}/events")
async def stream_session_events(session_id: str) -> StreamingResponse:
    """Live narration (Architecture.md: 'watch generation happen live') — open this BEFORE or
    DURING a POST .../turns call for the same session_id to see real events (ideation, routing,
    each Lead/specialist starting and completing, each real tool call) as that turn actually
    runs, ending with a `turn_completed` event. Does not replace that call's own response."""

    async def _sse_body():
        async for event in stream_events(session_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(_sse_body(), media_type="text/event-stream")
