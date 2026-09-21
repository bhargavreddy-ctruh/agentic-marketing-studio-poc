"""
Shared "pick the one specialist whose job matches this request" classifier — used by comment
resolution (services/canvas/comment_service.py) and targeted regeneration when the caller doesn't
already know which specialist to target. Architecture.md section 1c: "a comment... that the
orchestrator resolves as a scoped instruction against that specific specialist."

Separate from the Orchestrator's own `route()` classification (orchestrator.py) — that one also
decides between full_image/full_video/direct_fix in the same call; this one is used once the
caller already knows it's an element-scoped action and just needs to know WHO should handle it.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..specialists.registry import SPECIALIST_REGISTRY

log = get_logger(__name__)

_SYSTEM_PROMPT = """You are matching a user's request about an existing marketing asset to the ONE
specialist whose job matches it.

Available specialists, with the real tools each one can call:
{specialist_descriptions}

Cost and latency discipline (this matters — pick the CHEAPEST specialist that can genuinely
satisfy the request, never a more expensive one out of habit): a wrong price, discount, or other
text/number is a `text_overlay` + `discount_claims_calculator` job (overlay_artist) — cheap and
fast, never worth a full regeneration. A color/lighting/composition tweak is an `image_editor` job
— still cheap. Only pick a specialist whose only tool is a full generator
(`base_image_generator`/`base_video_generator`) when the request genuinely needs a brand new
composition that no targeted edit could produce.

Return ONLY JSON:
{{"target_specialist": "one of the exact names above, or empty string if none genuinely fits"}}
"""


def describe_specialists() -> str:
    """Public (not `_`-prefixed) since orchestrator.py's own classification prompt reuses this
    exact text too (Rules.md section 1: DRY) — both places need the same real tool-per-specialist
    picture to reliably route a cost-sensitive request (e.g. a wrong price) to the cheapest
    specialist that can fix it, not a full-regeneration one."""
    return "\n".join(
        f"- {name}: tools = {', '.join(spec.allowed_tools) or '(none — reasoning only)'}"
        for name, spec in sorted(SPECIALIST_REGISTRY.items())
    )


@traceable(name="specialist_classifier_node")
async def classify_target_specialist(message: str, *, extra_context: str = "") -> str | None:
    """Returns a real, registered specialist name, or None if the model couldn't confidently
    pick one (or both LLM gateways are down) — never a hallucinated or invalid name."""
    context = f"Request:\n{message}"
    if extra_context:
        context += f"\n\n{extra_context}"

    llm = get_llm_provider()
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_SYSTEM_PROMPT.format(specialist_descriptions=describe_specialists()),
            messages=[{"role": "user", "content": context}],
            max_tokens=1024,
            # Same category of real, live-found regression as orchestrator.py's own route()
            # classification (2026-09-21): a wrong specialist choice here misroutes a real edit,
            # so this exact-choice classification skips local-first routing too.
            prefer_local=False,
        )
        parsed = extract_json(result.text)
        target = str(parsed.get("target_specialist") or "").strip()
        if target not in SPECIALIST_REGISTRY:
            log.warning("specialist_classification_invalid", extra={"_extra_target": target})
            return None
        return target
    except (ProviderUnavailable, ValueError) as exc:
        log.warning("specialist_classification_unavailable", extra={"_extra_reason": str(exc)})
        return None
