"""
Visual Fidelity Checker — Architecture.md section 1a (trimmed Compliance set).

Real vision, when a real image is available (Memory.md, Phase 4): checks whether the product's
real must-show/never-show facts are ACTUALLY VISIBLE in the generated image, not just whether the
text prompt mentioned them — replacing the earlier text-only limitation, now the FALLBACK path
(video elements, or if the image can't be loaded). One gap remains even with vision: the full
architecture's "Faithful Upscaler comparison pass" — a pixel comparison against a real reference
product photo — still doesn't exist, since this POC has no product-photo upload endpoint to
compare against (Rules.md section 6). Vision closes "is this visible in the image," not "does it
match the real product's actual photo."
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable, SpecialistFailed
from ...core.json_extract import extract_json
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.llm.vision import complete_with_vision
from ...providers.observability.langsmith import traceable
from ..tools.product_lookup import ProductLookupTool

_VISION_SYSTEM_PROMPT = """You are the Visual Fidelity Checker for a marketing asset. You are given
the ACTUAL generated image and the product's real must-show/never-show facts. Look at the real
image and judge whether the must-show features are genuinely visible and no never-show items
appear. If product facts say "configured": false, there is nothing real to check against — pass by
default and say so honestly.

Return ONLY JSON:
{
  "passed": true,
  "reasoning": "one or two sentences describing what you actually saw",
  "violations": ["short phrases, empty list if passed"]
}
"""

_TEXT_FALLBACK_SYSTEM_PROMPT = """You are the Visual Fidelity Checker for a marketing asset.

You cannot see the actual pixels this time (no image available to check) — you are checking
whether the PROMPT used to generate it respects the product's real must-show/never-show facts. If
product facts say "configured": false, there is nothing real to check against — pass by default.

Return ONLY JSON:
{
  "passed": true,
  "reasoning": "one or two sentences",
  "violations": ["short phrases, empty list if passed"]
}
"""


@traceable(name="visual_fidelity_checker")
async def check_visual_fidelity(
    *, generation_prompt_text: str, image_bytes: bytes | None = None, mime_type: str | None = None
) -> dict:
    product_result = await ProductLookupTool().run({"question": generation_prompt_text[:500]})
    product_facts = product_result.data
    facts_context = f"Product facts (configured={product_facts.get('configured')}):\n{product_facts.get('facts', '')}"

    checked_with_vision = False
    try:
        if image_bytes is not None:
            result = await complete_with_vision(
                image_bytes=image_bytes,
                mime_type=mime_type or "image/jpeg",
                system=_VISION_SYSTEM_PROMPT,
                question=f"Are the must-show facts genuinely visible in this image, with no never-show items?\n\n{facts_context}",
            )
            checked_with_vision = True
        else:
            raise ProviderUnavailable("vision", "no image available for this element")
    except ProviderUnavailable:
        llm = get_llm_provider()
        context = f"Generation prompt used:\n{generation_prompt_text}\n\n{facts_context}"
        try:
            result = await llm.complete(
                tier=ModelTier.TIER_1,
                system=_TEXT_FALLBACK_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
                max_tokens=1024,
                # Same reasoning as brand_consistency_checker.py's own text-fallback call
                # (2026-09-21) — a compliance gate skips local-first routing.
                prefer_local=False,
            )
        except Exception as exc:
            raise SpecialistFailed("visual_fidelity_checker", str(exc)) from exc

    try:
        parsed = extract_json(result.text)
    except ValueError as exc:
        raise SpecialistFailed("visual_fidelity_checker", f"could not parse response: {exc}") from exc

    return {
        "passed": bool(parsed.get("passed", True)),
        "reasoning": str(parsed.get("reasoning", "")),
        "violations": [str(v) for v in (parsed.get("violations") or [])],
        "checked_against_configured_product": bool(product_facts.get("configured")),
        "checked_with_vision": checked_with_vision,
    }
