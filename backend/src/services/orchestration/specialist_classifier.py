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

from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..specialists.registry import SPECIALIST_REGISTRY

log = get_logger(__name__)

_SYSTEM_PROMPT = """<role>
You are the Target Classifier. Your job is to match a user's request about an existing marketing asset to the ONE specialist best suited to handle it.
</role>

<specialists>
Available specialists, along with the tools they can call:
{specialist_descriptions}
</specialists>

<rules>
1. **Best Capability Match:** Pick the specialist whose tools and description BEST match what the user is actually asking for. For image edits (recoloring, changing content, modifying subjects, adding/removing objects), pick a specialist with the `image_editor` tool (e.g., `composition_artist`). For text/price/discount overlays ONLY, pick `overlay_artist`. 
2. **Avoid Full Regeneration:** Only pick a specialist whose sole capability is full generation (`base_image_generator` or `base_video_generator`) if the request genuinely demands a brand new composition from scratch that no targeted edit could resolve.
</rules>

<output_format>
Return ONLY valid JSON matching this schema:
{{"target_specialist": "one of the exact names above, or empty string if none genuinely fits"}}
</output_format>
"""


def describe_specialists() -> str:
    """Public (not `_`-prefixed) since orchestrator.py's own classification prompt reuses this
    exact text too (Rules.md section 1: DRY) — both places need the same real tool-per-specialist
    picture to reliably route a request (e.g. an image edit vs a text overlay) to the specialist
    whose tools actually match what's being asked for.

    Includes each specialist's own `description` (not just its tool list) — a real, live-found gap
    (2026-09-21): a bare tool list let "make a video of it racing on track" get matched to
    video_editor_cutter purely because it has a video-shaped tool, when its real job (assembling
    clips that ALREADY exist) can't satisfy a request to generate brand new footage at all. The
    description is what actually disambiguates "generates X from scratch" from "edits an existing
    X" from "assembles/finishes an already-generated X"."""
    return "\n".join(
        f"- {name}: {spec.description or '(no description)'} "
        f"[tools = {', '.join(spec.allowed_tools) or 'none — reasoning only'}]"
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
            # Real live "thinking" text, per the user's explicit ask (2026-09-21).
            on_delta=lambda delta: emit("llm_delta", node="specialist_classifier", text=delta),
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
