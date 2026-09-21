"""
Visual Design Lead — Architecture.md section 1a: Reference Curator -> Palette Strategist ->
Illustrator -> Composition Artist, in that order (each depends on the previous one's output, so
this sequence is genuinely serial, not an arbitrary choice).

The fixed part of this file is WHICH SPECIALISTS RUN IN WHAT ORDER — the Lead's own definition,
per the reference architecture. WHICH TOOLS EACH SPECIALIST CALLS IS NOT FIXED (Memory.md, Phase 2
— the move to real agentic tool-calling): each specialist genuinely decides for itself whether and
which of its allowed_tools to invoke, verified live. This file no longer pre-fetches
brand_kit_lookup before Palette Strategist runs, and no longer parses an "image_prompt" field to
call base_image_generator itself — Palette Strategist and Illustrator make those calls themselves.

Deliberately does NOT touch the database — this function's job is generation only. The calling
SessionService persists the resulting CanvasElement, keeping "only repositories touch the
database" intact even though this file lives in services/ (Rules.md section 1).
"""
from __future__ import annotations

import json

from ...core.exceptions import SpecialistFailed
from ..specialists.runner import run_specialist_agentic
from .base import LeadResult, LeadSpec

VISUAL_DESIGN_LEAD = LeadSpec(
    name="visual_design_lead",
    specialist_sequence=(
        "reference_curator",
        "palette_strategist",
        "illustrator",
        "composition_artist",
    ),
    full_job_trigger="New still image from scratch",
)


async def run_visual_design_lead(*, brief: dict) -> LeadResult:
    """Runs the full 4-specialist sequence for one new still image. Returns a LeadResult with
    the final storage_ref, or raises SpecialistFailed if a step couldn't produce a usable result.
    """
    idea = brief.get("idea") or brief.get("initial_message") or ""

    reference = await run_specialist_agentic(
        "reference_curator", context=f"Campaign idea:\n{idea}\n\nBrief so far:\n{json.dumps(brief)}"
    )
    aesthetic_direction = reference.get("aesthetic_direction", "")

    palette = await run_specialist_agentic(
        "palette_strategist",
        context=f"Campaign idea:\n{idea}\n\nAesthetic direction:\n{aesthetic_direction}",
    )

    illustration = await run_specialist_agentic(
        "illustrator",
        context=(
            f"Campaign idea:\n{idea}\n\nAesthetic direction:\n{aesthetic_direction}\n\n"
            f"Palette direction:\n{palette.get('palette_direction', '')}"
        ),
    )
    image_call = illustration.latest_call("base_image_generator", "image_editor")
    if not image_call or not image_call.data.get("storage_ref"):
        raise SpecialistFailed("illustrator", "did not produce an image via base_image_generator")
    storage_ref = image_call.data["storage_ref"]
    image_prompt = illustration.get("image_prompt", "")
    # The real aspect_ratio actually sent to base_image_generator specifically (image_editor
    # doesn't change dimensions, so it never carries this arg) — what Format/Technical QA's
    # compliance check verifies the output against, not the LLM's own restated JSON field.
    generate_call = illustration.latest_call("base_image_generator")
    aspect_ratio = generate_call.args.get("aspect_ratio", "1:1") if generate_call else "1:1"

    composition = await run_specialist_agentic(
        "composition_artist",
        context=f"The generated image's storage_ref is: {storage_ref}\nIts prompt was:\n{image_prompt}",
    )
    edit_result = composition.latest_result("image_editor")
    if edit_result and edit_result.get("storage_ref"):
        storage_ref = edit_result["storage_ref"]

    all_steps = (reference, palette, illustration, composition)
    return LeadResult(
        storage_ref=storage_ref,
        produced_by_specialist="illustrator",
        element_type="image",
        metadata={
            "aesthetic_direction": aesthetic_direction,
            "palette_direction": palette.get("palette_direction", ""),
            "image_prompt": image_prompt,
            "aspect_ratio": aspect_ratio,
            "composition_edit_applied": bool(edit_result),
            # Genuine transparency about what each specialist actually chose to do this run —
            # not the same tool calls every time, since nothing here is hardcoded any more.
            "tool_calls_made": [
                {"specialist": step.specialist_name, "tool": call.tool_name, "ok": call.ok}
                for step in all_steps
                for call in step.tool_calls
            ],
        },
    )
