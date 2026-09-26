from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ....core.exceptions import NotFoundError
from ....services.knowledge.guardrail_service import GuardrailService
from ...dependencies import CurrentUserDep, SessionRepositoryDep

router = APIRouter(prefix="/api/v1/sessions/{session_id}/guardrails", tags=["Guardrails"])

def get_guardrail_service(repo: SessionRepositoryDep) -> GuardrailService:
    return GuardrailService(repo)


# Matches this codebase's established DI pattern (see api/dependencies.py's *Dep aliases) —
# `Depends(...)` as a plain function-argument default (ruff B008) works fine at runtime (FastAPI
# special-cases it), but this Annotated form is what every other route module already uses.
GuardrailServiceDep = Annotated[GuardrailService, Depends(get_guardrail_service)]


@router.get("", response_model=dict[str, Any])
async def get_guardrails(
    session_id: str,
    user_id: CurrentUserDep,
    svc: GuardrailServiceDep,
) -> dict[str, Any]:
    """Retrieve all guardrails for a session."""
    try:
        guardrail_set = await svc.get_or_derive_for_session(session_id)
        return guardrail_set.model_dump()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("", response_model=dict[str, Any])
async def update_guardrails(
    session_id: str,
    user_id: CurrentUserDep,
    payload: dict[str, Any],
    svc: GuardrailServiceDep,
) -> dict[str, Any]:
    """Update guardrails for a session."""
    try:
        updated_set = await svc.update_guardrails(session_id, payload)
        return updated_set.model_dump()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/link-profiles", response_model=dict[str, Any])
async def link_profiles(
    session_id: str,
    user_id: CurrentUserDep,
    payload: dict[str, Any],
    svc: GuardrailServiceDep,
) -> dict[str, Any]:
    """Real, live-found gap (2026-09-24, per an explicit user report): attaches a Brand DNA and/or
    Product DNA profile to this session so guardrails can actually be derived from real onboarded
    data — previously there was no way to link a profile to a session at all. Either key omitted
    (or `null`) leaves that linkage unchanged; an empty string clears it. Re-derives and MERGES
    guardrails from the newly-linked profile(s) into whatever the session already has."""
    try:
        updated_set = await svc.link_profiles(
            session_id,
            brand_profile_id=payload.get("brand_profile_id"),
            product_profile_id=payload.get("product_profile_id"),
        )
        return updated_set.model_dump()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("", response_model=dict[str, Any])
async def add_guardrail(
    session_id: str,
    user_id: CurrentUserDep,
    payload: dict[str, Any],
    svc: GuardrailServiceDep,
) -> dict[str, Any]:
    """Adds a new custom guardrail via LLM enhancement."""
    try:
        raw_rule = payload.get("rule")
        scope = payload.get("scope", "all")
        source = payload.get("source", "custom")
        
        if not raw_rule:
            raise ValueError("rule text is required")
            
        updated_set = await svc.add_rule_from_user_context(session_id, raw_rule, scope, source)
        return updated_set.model_dump()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
