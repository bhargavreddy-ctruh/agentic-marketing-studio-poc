"""
Brand Consistency Checker — Architecture.md section 1a (trimmed Compliance set).

Real vision, when a real image is available (Memory.md, Phase 4): uses a genuinely vision-capable
model (`providers/llm/vision.py`) to look at the actual generated pixels against real Brand DNA
facts — replacing the earlier text-only limitation ("checks the prompt, not the pixels"), which is
now the FALLBACK path only, used for video elements (no single frame to check yet) or if the image
can't be loaded. The result's `checked_with_vision` field makes which path ran visible to callers,
rather than letting a "passed: true" look more authoritative than it is either way.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable, SpecialistFailed
from ...core.json_extract import extract_json
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.llm.vision import complete_with_vision
from ...providers.observability.langsmith import traceable
from ..tools.brand_kit_lookup import BrandKitLookupTool

_VISION_SYSTEM_PROMPT = """You are the Brand Consistency Checker for a marketing asset. You are
given the ACTUAL generated image and the brand's real facts (colors, logo rules, prohibited
imagery). Look at the real image and judge whether it genuinely complies. If brand facts say
"configured": false, there is nothing real to check against — pass by default and say so honestly,
don't invent a violation just to have something to report.

Return ONLY JSON:
{
  "passed": true,
  "reasoning": "one or two sentences describing what you actually saw",
  "violations": ["short phrases, empty list if passed"]
}
"""

_TEXT_FALLBACK_SYSTEM_PROMPT = """You are the Brand Consistency Checker for a marketing asset.

You cannot see the actual pixels this time (no image available to check — e.g. a video element,
or the image failed to load) — you are checking whether the PROMPTS used to generate it are
consistent with the given brand facts. If brand facts say "configured": false, there is nothing
real to check against — pass by default and say so honestly, don't invent a violation.

Return ONLY JSON:
{
  "passed": true,
  "reasoning": "one or two sentences",
  "violations": ["short phrases, empty list if passed"]
}
"""


@traceable(name="brand_consistency_checker")
async def check_brand_consistency(
    *, generation_prompt_text: str, image_bytes: bytes | None = None, mime_type: str | None = None,
    user_id: str | None = None,
) -> dict:
    # Real, live-found bug (2026-09-25): this call used to pass no `context` at all, so
    # `BrandKitLookupTool` always hit its own `if not user_id: return configured: False` early
    # exit (brand_kit_lookup.py) — meaning this checker reported "no brand kit configured" for
    # EVERY element, even when the owning user has a real, fully onboarded brand.
    brand_result = await BrandKitLookupTool().run(
        {"question": generation_prompt_text[:500]}, context={"user_id": user_id} if user_id else None
    )
    brand_facts = brand_result.data
    facts_context = f"Brand facts (configured={brand_facts.get('configured')}):\n{brand_facts.get('facts', '')}"

    checked_with_vision = False
    try:
        if image_bytes is not None:
            result = await complete_with_vision(
                image_bytes=image_bytes,
                mime_type=mime_type or "image/jpeg",
                system=_VISION_SYSTEM_PROMPT,
                question=f"Do the real, visible contents of this image comply?\n\n{facts_context}",
            )
            checked_with_vision = True
        else:
            raise ProviderUnavailable("vision", "no image available for this element")
    except ProviderUnavailable:
        llm = get_llm_provider()
        context = f"Generation prompt(s) actually used:\n{generation_prompt_text}\n\n{facts_context}"
        try:
            result = await llm.complete(
                tier=ModelTier.TIER_1,
                system=_TEXT_FALLBACK_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
                max_tokens=1024,
            )
        except Exception as exc:
            raise SpecialistFailed("brand_consistency_checker", str(exc)) from exc

    try:
        parsed = extract_json(result.text)
    except ValueError as exc:
        raise SpecialistFailed("brand_consistency_checker", f"could not parse response: {exc}") from exc

    return {
        "passed": bool(parsed.get("passed", True)),
        "reasoning": str(parsed.get("reasoning", "")),
        "violations": [str(v) for v in (parsed.get("violations") or [])],
        "checked_against_configured_brand": bool(brand_facts.get("configured")),
        "checked_with_vision": checked_with_vision,
    }
