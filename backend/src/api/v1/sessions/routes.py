"""
HTTP only — validate, call the service, return (Rules.md section 2).

The SSE route (`GET /{session_id}/events`) is additive, not a replacement (core/events.py) — a
client already knows the session_id before either `POST /{session_id}/turns` OR `POST /` (which,
since 2026-09-21, only creates the row — see `SessionService.create_session`'s docstring for why),
so it can open the event stream just before/during either call to watch every turn happen live,
first turn included.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from ....core.events import stream_events
from ....schemas.sessions.requests import CreateSessionRequest, PostTurnRequest, UpdateApprovalModeRequest, UpdateDnaRequest
from ....schemas.sessions.responses import ChatTurnResponse, SessionResponse
from ...dependencies import CurrentUserDep, SessionServiceDep

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse)
async def create_session(
    body: CreateSessionRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    return await svc.create_session(
        user_id=current_user.id, approval_mode=body.approval_mode, title=body.title
    )


@router.get("", response_model=list[SessionResponse])
async def list_sessions(svc: SessionServiceDep, current_user: CurrentUserDep) -> list[SessionResponse]:
    """The current user's own workflows only (Tasks_Workflows.md #2) — the home/workflow-selection
    page's real data source."""
    return await svc.list_sessions(user_id=current_user.id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    return await svc.get_session(session_id, user_id=current_user.id)


@router.put("/{session_id}/approval-mode", response_model=SessionResponse)
async def update_approval_mode(
    session_id: str, body: UpdateApprovalModeRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    """Real, live-found gap (2026-09-24, per an explicit user ask: "in chat box user should be
    able to select the mode(auto/approve mode)") — mode selection previously only existed on the
    home page's "new workflow" form, before a session even existed; there was no way to change it
    for an already-running conversation."""
    return await svc.update_approval_mode(session_id, user_id=current_user.id, approval_mode=body.approval_mode)


@router.put("/{session_id}/dna", response_model=SessionResponse)
async def update_dna(
    session_id: str, body: UpdateDnaRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    """Accepts raw text for Brand and Product DNA, saves it to the session, and triggers
    rule extraction so downstream agents can follow them immediately."""
    session = await svc.get_session(session_id, user_id=current_user.id)
    session_model = await svc._sessions.get(session_id)
    
    session_model.brief["brand_dna"] = body.brand_dna or ""
    session_model.brief["product_dna"] = body.product_dna or ""
    await svc._sessions.update(session_model)
    
    from ....services.knowledge.guardrail_service import GuardrailService
    guardrail_svc = GuardrailService(svc._sessions)
    
    existing = await guardrail_svc.get_or_derive_for_session(session_id)
    human_rules = [r for r in existing.rules if r.source not in ("brand", "product")]
    existing.rules = human_rules
    await guardrail_svc.update_guardrails(session_id, existing.model_dump())
    
    if body.brand_dna:
        await guardrail_svc.add_rule_from_user_context(session_id, body.brand_dna, scope="all", source="brand")
    if body.product_dna:
        await guardrail_svc.add_rule_from_user_context(session_id, body.product_dna, scope="all", source="product")
        
    return await svc.get_session(session_id, user_id=current_user.id)


@router.post("/{session_id}/turns", response_model=SessionResponse)
async def post_turn(
    session_id: str, body: PostTurnRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    return await svc.post_turn(
        session_id,
        user_id=current_user.id,
        picked_option_id=body.picked_option_id,
        free_text=body.free_text,
        referenced_element_ids=body.referenced_element_ids,
    )


from ....services.orchestration.session_service import cancel_running_turn

@router.post("/{session_id}/cancel")
async def cancel_turn(
    session_id: str, svc: SessionServiceDep, current_user: CurrentUserDep
):
    await svc.get_session(session_id, user_id=current_user.id)
    cancelled = cancel_running_turn(session_id)
    return {"cancelled": cancelled}


@router.get("/{session_id}/turns", response_model=list[ChatTurnResponse])
async def list_turns(
    session_id: str, svc: SessionServiceDep, current_user: CurrentUserDep
) -> list[ChatTurnResponse]:
    """Real, persisted chat history (2026-09-22) — the actual fix for a page refresh losing the
    conversation. Every prior turn's real user message, the real "thinking" text streamed live
    during it, and the real final response — ownership-checked like every other session route."""
    return await svc.list_turns(session_id, user_id=current_user.id)


@router.get("/{session_id}/events")
async def stream_session_events(
    session_id: str, svc: SessionServiceDep, current_user: CurrentUserDep
) -> StreamingResponse:
    """Live narration (Architecture.md: 'watch generation happen live') — open this BEFORE or
    DURING a POST .../turns call for the same session_id to see real events (ideation, routing,
    each Lead/specialist starting and completing, each real tool call) as that turn actually
    runs, ending with a `turn_completed` event. Does not replace that call's own response.

    Ownership-checked (Tasks_Workflows.md #2) via the same real lookup `get_session` does — a live
    event stream is real, session-scoped information, not something a different user should be
    able to watch just by knowing the id."""
    await svc.get_session(session_id, user_id=current_user.id)

    async def _sse_body():
        async for event in stream_events(session_id):
            yield f"data: {json.dumps(event)}\n\n"

    # Defensive hardening (2026-09-24, alongside the real `llm_retry`/`llm_provider_fallback`
    # events — see `_openai_compatible.py`/`router.py`): explicit no-buffering headers so no
    # intermediate proxy/cache between here and the browser can batch/delay real event delivery.
    # Not the real cause of the reported lag (that was genuine silence during provider retries,
    # now fixed at the source), but a real, cheap correctness fix with no downside either way.
    return StreamingResponse(
        _sse_body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
