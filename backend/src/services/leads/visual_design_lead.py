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
from ..specialists.runner import run_specialist_agentic, run_specialist_with_review
from .base import LeadResult, LeadSpec, referenced_element_block, stale_campaign_context_block

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


async def run_visual_design_lead(*, brief: dict, user_message: str = "") -> LeadResult:
    """Runs the full 4-specialist sequence for one new still image. Returns a LeadResult with
    the final storage_ref, or raises SpecialistFailed if a step couldn't produce a usable result.

    `user_message` — the caller's OWN literal latest message, added 2026-09-22 — a real, live-found
    bug: this function used to receive ONLY `brief`, never the actual current request text.
    `brief.idea` is a real, merged synthesis, but Ideation deliberately SKIPS re-merging it once any
    element already exists in the session (ideation_service.py's own documented fix for an earlier
    numeric-erosion bug) — so for every follow-up turn, `brief.idea` stays FROZEN at whatever it was
    when the session's first element was created. A genuinely new, unrelated request in the SAME
    session (e.g. "generate 2 images of a ferrari" typed into a session whose `brief.idea` is still
    "a modern minimalist logo" from much earlier) used to generate against that stale idea alone —
    the new request's actual subject never reached the image generator's own context at all,
    producing a confidently-rendered image of the WRONG thing. The literal current message is now
    the PRIMARY driver whenever present, with `brief.idea` demoted to supporting context — the same
    "current request first, broader context second" shape `_direct_fix_node`/`_full_audio_node`
    already used correctly."""
    if user_message.strip():
        idea = user_message.strip() + stale_campaign_context_block(brief)
    else:
        idea = brief.get("idea") or brief.get("initial_message") or ""

    # `referenced_element_block` explicitly called out here (2026-09-22), not just left buried in
    # the raw `json.dumps(brief)` dump below — see that helper's own docstring for the real,
    # live-found reason (base.py). The full brief dump stays too, for every OTHER scratch field
    # Reference Curator might genuinely need.
    reference = await run_specialist_agentic(
        "reference_curator",
        context=f"Campaign idea:\n{idea}{referenced_element_block(brief)}\n\nBrief so far:\n{json.dumps(brief)}",
    )
    aesthetic_direction = reference.get("aesthetic_direction", "")

    palette = await run_specialist_agentic(
        "palette_strategist",
        context=f"Campaign idea:\n{idea}\n\nAesthetic direction:\n{aesthetic_direction}",
    )

    illustrator_context = (
        f"Campaign idea:\n{idea}\n\nAesthetic direction:\n{aesthetic_direction}\n\n"
        f"Palette direction:\n{palette.get('palette_direction', '')}"
    )
    # Real, live-found failure mode (2026-09-21): a smaller model — specifically router.py's local
    # last-resort fallback, confirmed live at roughly a 1-in-3 rate even after tightening
    # illustrator.md's own wording — sometimes stops after its two lookup tool calls without ever
    # generating the image. `run_specialist_with_review` (Tasks.md #3, 2026-09-22) generalizes the
    # one bounded corrective retry this used to hand-roll here — same guardrail philosophy as
    # discount_math_calculator (never just trust the prompt), now shared across every Lead instead
    # of duplicated per call site.
    illustration = await run_specialist_with_review(
        "illustrator",
        context=illustrator_context,
        needs_retry=lambda r: not (
            r.latest_call("base_image_generator", "image_editor")
            and r.latest_call("base_image_generator", "image_editor").data.get("storage_ref")
        ),
        reminder=(
            "REMINDER: your previous attempt looked up brand/product facts but never actually "
            "called base_image_generator. You MUST call base_image_generator with your image "
            "prompt now, before responding with final JSON."
        ),
    )
    image_call = illustration.latest_call("base_image_generator", "image_editor")
    if not image_call or not image_call.data.get("storage_ref"):
        raise SpecialistFailed(
            "illustrator", "did not produce an image via base_image_generator (after one review retry)"
        )
    storage_ref = image_call.data["storage_ref"]
    image_prompt = illustration.get("image_prompt", "")
    # The real aspect_ratio actually sent to base_image_generator specifically (image_editor
    # doesn't change dimensions, so it never carries this arg) — what Format/Technical QA's
    # compliance check verifies the output against, not the LLM's own restated JSON field.
    generate_call = illustration.latest_call("base_image_generator")
    aspect_ratio = generate_call.args.get("aspect_ratio", "1:1") if generate_call else "1:1"

    composition = await run_specialist_agentic(
        "composition_artist",
        context=(
            f"The generated image's storage_ref is: {storage_ref}\nIts prompt was:\n{image_prompt}\n\n"
            f"Aesthetic direction:\n{aesthetic_direction}\n\n"
            f"Palette direction:\n{palette.get('palette_direction', '')}"
        ),
    )
    edit_result = composition.latest_result("image_editor")
    if edit_result and edit_result.get("storage_ref"):
        storage_ref = edit_result["storage_ref"]

    all_steps = (reference, palette, illustration, composition)
    # A real, visible "creative brief" text card (2026-09-22) — the reference product this POC is
    # modeled on shows exactly this kind of card on its own canvas. Written by Composition Artist
    # itself via a genuine `text_card_writer` tool call (a real, modular tool, per an explicit user
    # ask — the same agentic pattern every other generation capability already uses), not Python
    # code hand-building the string after the fact. Falls back to a plain hand-built summary only
    # if the specialist genuinely skipped the tool call (a real model can be flaky about optional
    # instructions) — an honest degrade, never a missing card.
    brief_call = composition.latest_call("text_card_writer")
    if brief_call and brief_call.data.get("storage_ref"):
        creative_brief_extra = {
            "storage_ref": brief_call.data["storage_ref"],
            "element_type": "text",
            "produced_by_specialist": "composition_artist",
            "metadata": {"label": "creative_brief"},
        }
    else:
        creative_brief_extra = {
            "element_type": "text",
            "produced_by_specialist": "composition_artist",
            "metadata": {
                "label": "creative_brief",
                "text": (
                    f"Aesthetic direction:\n{aesthetic_direction}\n\n"
                    f"Palette direction:\n{palette.get('palette_direction', '')}\n\n"
                    f"Image prompt used:\n{image_prompt}"
                ),
            },
        }
    return LeadResult(
        storage_ref=storage_ref,
        produced_by_specialist="illustrator",
        element_type="image",
        extra_elements=[creative_brief_extra],
        metadata={
            "aesthetic_direction": aesthetic_direction,
            "palette_direction": palette.get("palette_direction", ""),
            "image_prompt": image_prompt,
            "aspect_ratio": aspect_ratio,
            "composition_edit_applied": bool(edit_result),
            # Ideation's own one-off mood/style announcement for this turn, if it made one
            # (ideation_service.py) — carried through metadata so session_service.py can surface it
            # to the user the same way it already surfaces partial_generation_note.
            **({"style_note": brief["style_note"]} if brief.get("style_note") else {}),
            # Genuine transparency about what each specialist actually chose to do this run —
            # not the same tool calls every time, since nothing here is hardcoded any more.
            "tool_calls_made": [
                {"specialist": step.specialist_name, "tool": call.tool_name, "ok": call.ok}
                for step in all_steps
                for call in step.tool_calls
            ],
        },
    )
