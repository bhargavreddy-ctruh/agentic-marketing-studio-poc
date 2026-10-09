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
from typing import Annotated

from fastapi import APIRouter, Cookie, Query, status
from starlette.responses import Response, StreamingResponse

from ....core.events import stream_events
from ....core.exceptions import Unauthorized
from ....core.middleware.logging import get_logger
from ....models.base import async_session_factory
from ....schemas.sessions.requests import (
    CreateSessionRequest,
    PostTurnRequest,
    SelectBrandRequest,
    UpdateApprovalModeRequest,
    UpdateDnaRequest,
    UpdateGuardrailsEnabledRequest,
    UpdateTitleRequest,
)
from ....schemas.sessions.responses import ChatTurnResponse, SessionResponse
from ...dependencies import (
    SESSION_COOKIE_NAME,
    BrandDnaServiceDep,
    CurrentUserDep,
    SessionServiceDep,
    get_current_user,
    get_session_service,
)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
log = get_logger(__name__)


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


@router.put("/{session_id}/guardrails-enabled", response_model=SessionResponse)
async def update_guardrails_enabled(
    session_id: str, body: UpdateGuardrailsEnabledRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    """Per-session guardrails on/off (2026-09-25, explicit user ask: "add a toggle to turn off
    guardrails if user wants to") — same shape as `update_approval_mode` above."""
    return await svc.update_guardrails_enabled(
        session_id, user_id=current_user.id, guardrails_enabled=body.guardrails_enabled
    )


@router.put("/{session_id}/brand", response_model=SessionResponse)
async def select_brand(
    session_id: str, body: SelectBrandRequest, svc: SessionServiceDep,
    brand_svc: BrandDnaServiceDep, current_user: CurrentUserDep,
) -> SessionResponse:
    """New (2026-10-05, explicit user ask: a brand picker dropdown in the Brand DNA tab) — switches
    this session to one of the user's own already-scraped/saved brands, no re-crawl needed.
    `brand_svc.get_brand` is the real ownership gate: it already raises Forbidden/NotFound if
    `brand_id` doesn't belong to `current_user` (same check `upload_brand_logo` etc. already use) —
    checked BEFORE `select_brand` ever touches the session, so this can never link a session to
    another user's brand."""
    await brand_svc.get_brand(body.brand_id, user_id=current_user.id)
    return await svc.select_brand(session_id, user_id=current_user.id, brand_id=body.brand_id)


@router.put("/{session_id}/title", response_model=SessionResponse)
async def update_title(
    session_id: str, body: UpdateTitleRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    """Rename a workflow (2026-09-30, explicit user ask: "add a delete/edit button on
    workflows") — same shape as `update_approval_mode` above."""
    return await svc.update_title(session_id, user_id=current_user.id, title=body.title)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, svc: SessionServiceDep, current_user: CurrentUserDep
) -> Response:
    """Permanently deletes a workflow and everything on it (2026-09-30, explicit user ask) —
    ownership-checked like every other session route (`svc.delete_session` raises
    Forbidden/NotFoundError before deleting anything)."""
    await svc.delete_session(session_id, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{session_id}/dna", response_model=SessionResponse)
async def update_dna(
    session_id: str, body: UpdateDnaRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    """Real, live-found gap (2026-09-25, explicit user ask: "connect this also to the place
    you're saving these deets") — this route used to only write loose, un-namespaced guardrail
    rule TEXT via `add_rule_from_user_context`, completely disconnected from the real
    `BrandProfileModel`/`ProductProfileModel` rows the chat- and canvas-driven paths save to (and,
    since the `get_or_derive_for_session` rewrite, those loose rules got silently wiped on the very
    next re-derivation anyway — wasted work). Now saves into the SAME real tables, with the SAME
    scoping rule as everywhere else in this app: Brand DNA is per-USER (updates the user's one
    existing brand, shared across every one of their sessions, never a new row per session);
    Product DNA is per-SESSION (updates only this session's linked product, or creates one linked
    only here)."""
    await svc.get_session(session_id, user_id=current_user.id)  # ownership check (raises Forbidden/NotFoundError)
    session_model = await svc._sessions.get(session_id)

    new_brief = dict(session_model.brief)
    # Real, live-found bug (2026-09-25, caught by reading a real user's session): the frontend form
    # always sends all three *Details objects on every save (whichever tab was actually edited), so
    # `is not None` was always true — a save from the Product tab alone silently overwrote a
    # genuinely-saved Campaign/Brand tab with blank strings, confirmed live in this exact DB. Only
    # persist a *Details object when it actually has real content in at least one field.
    if body.campaignDetails is not None and any(
        v.strip() for v in (body.campaignDetails.campaignIdea, body.campaignDetails.audience, body.campaignDetails.goal) if v
    ):
        new_brief["campaignDetails"] = body.campaignDetails.model_dump()
    if body.brandDetails is not None and any(
        v.strip() for v in (body.brandDetails.voiceAndTone, body.brandDetails.visualIdentity, body.brandDetails.logoRules) if v
    ):
        new_brief["brandDetails"] = body.brandDetails.model_dump()
    if body.productDetails is not None and any(
        v.strip() for v in (body.productDetails.name, body.productDetails.category, body.productDetails.productDescription) if v
    ):
        new_brief["productDetails"] = body.productDetails.model_dump()
    session_model.brief = new_brief

    from ....models.base import async_session_factory
    from ....repositories.postgres.postgres_brand_repository import PostgresBrandRepository
    from ....repositories.postgres.postgres_product_repository import PostgresProductRepository
    from ....services.knowledge.brand_dna_service import BrandDnaService
    from ....services.knowledge.product_dna_service import ProductDnaService

    async with async_session_factory() as db:
        brand_repo = PostgresBrandRepository(db)
        product_repo = PostgresProductRepository(db)
        brand_svc = BrandDnaService(brand_repo)
        product_svc = ProductDnaService(product_repo)

        bd = body.brandDetails
        if bd is not None and (bd.voiceAndTone or bd.visualIdentity or bd.logoRules):
            # Real, live-found bug (2026-09-26, root-caused via a real corrupted brand row found
            # this session: name "Bewakoof" but a business overview describing "Infinix" — a
            # completely different brand): this used to fall back to
            # `brand_repo.list_for_user(current_user.id)[0]` — the user's FIRST EVER brand across
            # ALL their sessions — and MERGE new facts into that same row whenever THIS session
            # didn't already have its own `brand_profile_id` set. Two different sessions onboarding
            # different real brands both hit this same fallback and got merged into one
            # contaminated row. `session.brand_profile_id` is already a single nullable field —
            # "one brand per session" only holds in practice if a session with none yet gets a
            # genuinely NEW row, never an arbitrary existing one borrowed from elsewhere.
            existing_brand = None
            existing_brand_id = session_model.brand_profile_id
            if existing_brand_id:
                existing_brand = await brand_repo.get(existing_brand_id)
            raw_facts = {
                k: v for k, v in {
                    "Voice and Tone": bd.voiceAndTone,
                    "Visual Identity & Colors": bd.visualIdentity,
                    "Logo Rules & Constraints": bd.logoRules,
                }.items() if v
            }
            brand = await brand_svc.onboard_brand(
                user_id=current_user.id,
                # This simplified form has no "Brand Name" field at all — preserve THIS session's
                # own existing brand's real name if it already has one (an edit, not a fresh
                # onboard); a session with no brand yet always gets a placeholder name, never
                # another session's real name.
                name=existing_brand.name if existing_brand else "Untitled Brand",
                raw_facts=raw_facts,
                brand_id=existing_brand.id if existing_brand else None,
            )
            session_model.brand_profile_id = brand.id

        pd = body.productDetails
        if pd is not None and (pd.name or pd.productDescription):
            linked_ids = list(session_model.brief.get("product_profile_ids") or [])
            existing_product_id = linked_ids[0] if linked_ids else session_model.product_profile_id
            existing_product = await product_repo.get(existing_product_id) if existing_product_id else None
            description = "\n".join(
                part for part in (f"Category: {pd.category}" if pd.category else None, pd.productDescription) if part
            )
            product = await product_svc.onboard_product(
                user_id=current_user.id,
                name=pd.name or (existing_product.name if existing_product else "Unnamed product"),
                description=description,
                price=None,
                discount_percent=None,
                product_id=existing_product.id if existing_product else None,
            )
            if product.id not in linked_ids:
                linked_ids.append(product.id)
            session_model.brief = {**session_model.brief, "product_profile_ids": linked_ids}
            session_model.product_profile_id = product.id

    await svc._sessions.update(session_model)

    from ....services.knowledge.guardrail_service import GuardrailService
    guardrail_svc = GuardrailService(svc._sessions)
    await guardrail_svc.get_or_derive_for_session(session_id)

    return await svc.get_session(session_id, user_id=current_user.id)


from pydantic import BaseModel


class UpdateStyleRequest(BaseModel):
    style_ref_storage_ref: str | None = None
    style_seed: int | None = None


@router.put("/{session_id}/style", response_model=SessionResponse)
async def update_session_style(
    session_id: str,
    body: UpdateStyleRequest,
    svc: SessionServiceDep,
    current_user: CurrentUserDep,
) -> SessionResponse:
    await svc.get_session(session_id, user_id=current_user.id)
    session_model = await svc._sessions.get(session_id)
    
    if body.style_ref_storage_ref is not None:
        session_model.style_ref_storage_ref = body.style_ref_storage_ref
    if body.style_seed is not None:
        session_model.style_seed = body.style_seed
        
    await svc._sessions.update(session_model)
    return await svc.get_session(session_id, user_id=current_user.id)


@router.post("/{session_id}/turns", response_model=SessionResponse)
async def post_turn(
    session_id: str, body: PostTurnRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> SessionResponse:
    """Fire-and-forget turn dispatch (2026-10-06) — validates, marks status='generating', and
    returns immediately (milliseconds).  The actual generation runs as a detached asyncio task
    with its own DB session via `begin_turn` / `_run_turn_bg`.  Progress is streamed live over
    SSE (`GET /{session_id}/events`); the frontend fetches `GET /{session_id}` once the
    `turn_completed` event arrives to read the final SessionResponse.  This eliminates the
    30-second Next.js dev-proxy timeout entirely."""
    return await svc.begin_turn(
        session_id,
        user_id=current_user.id,
        picked_option_id=body.picked_option_id,
        free_text=body.free_text,
        referenced_element_ids=body.referenced_element_ids,
        target_product_id=body.target_product_id,
        start_new_product=body.start_new_product,
        edited_plan=[step.model_dump() for step in body.edited_plan] if body.edited_plan else None,
    )


from ....services.orchestration.session_service import cancel_running_turn


@router.post("/{session_id}/cancel")
async def cancel_turn(
    session_id: str, svc: SessionServiceDep, current_user: CurrentUserDep
):
    session_model = await svc._get_owned_session(session_id, user_id=current_user.id)
    cancelled = cancel_running_turn(session_id)
    
    # If the database thinks it's generating but there is no running task
    # (e.g. because of a server restart), forcefully reset it.
    if session_model.status == "generating":
        session_model.status = "error"
        session_model.next_prompt_json = {
            "message": "Generation cancelled by user.",
            "options": [],
            "allow_free_text": True,
        }
        await svc._sessions.update(session_model)
        from ....core.events import mark_turn_done
        await mark_turn_done(session_id)
        cancelled = True

    return {"cancelled": cancelled}


@router.get("/{session_id}/turns", response_model=list[ChatTurnResponse])
async def list_turns(
    session_id: str,
    svc: SessionServiceDep,
    current_user: CurrentUserDep,
    limit: int | None = Query(default=None, gt=0, le=200),
    before_id: str | None = Query(default=None),
) -> list[ChatTurnResponse]:
    """Real, persisted chat history (2026-09-22) — the actual fix for a page refresh losing the
    conversation. Every prior turn's real user message, the real "thinking" text streamed live
    during it, and the real final response — ownership-checked like every other session route.

    `limit`/`before_id` (2026-10-05, chat lazy-load): both optional and unset by default, so an
    omitted query string still returns the full history exactly as before. Pass `limit` alone for
    "the most recent N turns"; add `before_id` (an already-loaded turn's id) to page further back
    in history from there. Returned turns are always in ascending (oldest-first) order either way."""
    return await svc.list_turns(session_id, user_id=current_user.id, limit=limit, before_id=before_id)


@router.get("/{session_id}/events")
async def stream_session_events(
    session_id: str,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> StreamingResponse:
    """Live narration (Architecture.md: 'watch generation happen live') — open this BEFORE or
    DURING a POST .../turns call for the same session_id to see real events (ideation, routing,
    each Lead/specialist starting and completing, each real tool call) as that turn actually
    runs, ending with a `turn_completed` event. Does not replace that call's own response.

    Ownership-checked (Tasks_Workflows.md #2) via the same real lookup `get_session` does — a live
    event stream is real, session-scoped information, not something a different user should be
    able to watch just by knowing the id.

    Real, live-found fix (2026-10-05): this route used to take `svc: SessionServiceDep` /
    `current_user: CurrentUserDep` like every other route — both depend on the request-scoped
    `DbSession` (`Depends(get_session)`). FastAPI's own documented gotcha: a `yield`-based
    dependency's cleanup (closing the session, returning its connection to the pool) only runs
    AFTER the full response is sent — for a `StreamingResponse`, that means the ENTIRE SSE stream's
    lifetime, not just the quick ownership check above. With `pool_size=3, max_overflow=2` (5 total
    — see `models/base.py`'s own docstring on why it's kept this small for Supabase), it took only
    a handful of concurrently open chat tabs to exhaust the WHOLE pool for every other endpoint in
    the app — confirmed live: a sustained burst of `QueuePool limit ... connection timed out`
    across completely unrelated routes (auth, canvas, brand lists), surfaced to the user as a flat
    "Network error — is the backend running?" / "Failed to fetch". Fixed by doing the ownership
    check inside its own explicitly short-lived session (`async_session_factory()`, opened and
    closed BEFORE the `StreamingResponse` begins), reusing the same `get_current_user`/
    `get_session_service` dependency FUNCTIONS directly rather than through FastAPI's `Depends` — so
    no connection is held for the stream's duration."""
    if session_token is None:
        raise Unauthorized()
    async with async_session_factory() as db:
        current_user = await get_current_user(db, session_token)
        svc = get_session_service(db)
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


import asyncio

from ....schemas.crawlers.requests import CrawlRequest
from ....services.crawlers.crawl_runner import run_crawl_and_ingest

_crawl_tasks: set[asyncio.Task] = set()  # same asyncio.create_task + tracking-set pattern as
                                          # session_service.py's _background_tasks


@router.post("/{session_id}/crawl")
async def crawl_url(
    session_id: str, body: CrawlRequest, svc: SessionServiceDep, current_user: CurrentUserDep
) -> dict:
    """Product/Brand crawler (2026-09-28) — background-dispatched, fire-and-forget: this returns
    immediately, and progress/results surface purely via the existing SSE `crawler_*` events (see
    `GET /{session_id}/events`)."""
    await svc.get_session(session_id, user_id=current_user.id)  # ownership check
    task = asyncio.create_task(run_crawl_and_ingest(session_id, body.url, url_type=body.url_type))
    _crawl_tasks.add(task)
    task.add_done_callback(_crawl_tasks.discard)
    return {"status": "started", "url": body.url}
