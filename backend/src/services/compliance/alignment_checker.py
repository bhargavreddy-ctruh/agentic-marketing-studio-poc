from __future__ import annotations

from ...core.middleware.logging import get_logger
from ...providers.llm.laya_provider import LayaProvider

log = get_logger(__name__)


async def check_alignment(user_message: str, generation_prompt_text: str) -> dict:
    """
    Uses the fast Laya Decision Model to score if the generation output is strictly aligned 
    with the user's request.
    
    Returns a compliance check dict with a 'passed' boolean and any violations.
    """
    if not user_message or not generation_prompt_text:
        return {"passed": True, "violations": []}
        
    state = f"User Request: {user_message}\nGenerated Output Context: {generation_prompt_text}"
    statement = "The generated output completely fulfills and aligns with the user's explicit request."
    
    try:
        prob = await LayaProvider.predict_noul(state, statement_context=statement)
        log.info("laya_alignment_score", extra={"_extra_prob": prob})
        
        # If probability is less than 0.4, it's flagged as misaligned
        if prob < 0.4:
            return {
                "passed": False, 
                "violations": [
                    f"Warning: The generated output may not align with your intended request. (Confidence: {prob:.2f})"
                ],
                "alignment_score": prob
            }
        return {"passed": True, "violations": [], "alignment_score": prob}
    except Exception as e:
        log.warning("alignment_check_failed", extra={"_extra_error": str(e)})
        return {"passed": True, "violations": []}
