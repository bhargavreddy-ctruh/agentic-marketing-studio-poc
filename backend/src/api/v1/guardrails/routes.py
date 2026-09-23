from fastapi import APIRouter, Depends, HTTPException, status
from typing import Any

from ....core.exceptions import NotFoundError, ValidationFailed
from ....services.knowledge.guardrail_service import GuardrailService
from ...dependencies import CurrentUserDep, SessionRepositoryDep

router = APIRouter(prefix="/api/v1/sessions/{session_id}/guardrails", tags=["Guardrails"])

def get_guardrail_service(repo: SessionRepositoryDep) -> GuardrailService:
    return GuardrailService(repo)


@router.get("", response_model=dict[str, Any])
async def get_guardrails(
    session_id: str,
    user_id: CurrentUserDep,
    svc: GuardrailService = Depends(get_guardrail_service),
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
    svc: GuardrailService = Depends(get_guardrail_service),
) -> dict[str, Any]:
    """Update guardrails for a session."""
    try:
        updated_set = await svc.update_guardrails(session_id, payload)
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
    svc: GuardrailService = Depends(get_guardrail_service),
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
