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
from ...core.middleware.logging import get_logger
from ..specialists.runner import (
    AgenticStepResult,
    deliverable_hint_block,
    run_specialist_agentic,
    run_specialist_with_review,
)
from .base import (
    LeadResult,
    LeadSpec,
    referenced_element_block,
    stale_campaign_context_block,
)

log = get_logger(__name__)

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
        brief=brief,
    )
    aesthetic_direction = reference.get("aesthetic_direction", "")

    # `referenced_element_block` here too (2026-09-25) — Palette Strategist previously had no
    # storage_ref anywhere in its context, so it could never actually analyze the real referenced
    # image (`runner.py`'s reference_storage_ref auto-injection into tool context also only
    # activates `if brief:`, so `brief=brief` below is a real prerequisite, not just for chat
    # history). See visual_palette_analyzer.py for what it can now do with that storage_ref.
    palette = await run_specialist_agentic(
        "palette_strategist",
        context=(
            f"Campaign idea:\n{idea}\n\nAesthetic direction:\n{aesthetic_direction}"
            f"{referenced_element_block(brief)}"
        ),
        brief=brief,
    )

    illustrator_context = (
        f"Campaign idea:\n{idea}\n\nAesthetic direction:\n{aesthetic_direction}\n\n"
        f"Palette direction:\n{palette.get('palette_direction', '')}\n\n"
        f"Brief so far:\n{json.dumps(brief)}"
        f"{referenced_element_block(brief)}"
        f"{deliverable_hint_block(brief)}"
    )
    # One-pass product compositing: if a product photo exists, tell the illustrator to pass it as
    # reference_storage_ref to base_image_generator. Qwen image-to-image generates the background
    # scene WITH the product naturally lit and composited in a single API call — no separate
    # rembg/PIL step needed, saving one Qwen credit and producing far better lighting/shadow
    # integration than a post-hoc pixel paste could achieve.
    product_photo_ref = brief.get("product_photo_storage_ref")
    if product_photo_ref:
        illustrator_context += (
            f"\n\nPRODUCT PHOTO AVAILABLE — storage_ref: {product_photo_ref}\n"
            "You MUST pass this storage_ref as 'reference_storage_ref' to base_image_generator so the "
            "product is naturally composited INTO the generated scene in a single pass. "
            "Write the prompt to describe the full scene (background + product placement), not the product alone."
        )
    # Real, live-found bug (2026-09-30): a collab campaign image rendered literal placeholder
    # text ("NOTHING LOGO") instead of the brand's real uploaded logo — illustrator's own prompt
    # already knows to combine 2+ real assets via collab_image_generator when both exist, but
    # never had the real logo storage_ref to pass, only prose brand facts, so it could only ever
    # describe/hallucinate a logo in words. Same explicit text-injection pattern as the product
    # photo above — collab_image_generator requires the ref as a literal arg, not a ctx fallback.
    brand_logo_ref = brief.get("brand_logo_storage_ref")
    if brand_logo_ref:
        illustrator_context += (
            f"\n\nBRAND LOGO AVAILABLE — storage_ref: {brand_logo_ref}\n"
            "This is the brand's REAL, actual uploaded logo image — not a description. Whenever this "
            "generation is already image-to-image/collab (a product photo or other real reference is "
            "already involved), OR the request explicitly asks for the real logo/brand asset to be "
            "used, you MUST use collab_image_generator with this storage_ref included in "
            "reference_storage_refs (alongside the product photo ref above, when both apply) — never "
            "merely describe the logo in words in that case. A pure text-to-image generation with no "
            "other real reference involved may still describe the logo stylistically if collab isn't "
            "otherwise warranted."
        )
    # Real, live-found failure mode (2026-09-21): a smaller model — specifically router.py's local
    # last-resort fallback, confirmed live at roughly a 1-in-3 rate even after tightening
    # illustrator.md's own wording — sometimes stops after its two lookup tool calls without ever
    # generating the image. `run_specialist_with_review` (Tasks.md #3, 2026-09-22) generalizes the
    # one bounded corrective retry this used to hand-roll here — same guardrail philosophy as
    # discount_math_calculator (never just trust the prompt), now shared across every Lead instead
    # of duplicated per call site.
    try:
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
            brief=brief,
        )
    except SpecialistFailed as exc:
        # If illustrator generated an asset via base_image_generator but THEN failed (e.g. guardrail conflict),
        # we want to surface the error BUT STILL KEEP the generated asset.
        illustration = getattr(exc, "partial_result", None)
        if not illustration or not illustration.latest_call("base_image_generator", "image_editor"):
            raise

        # We have a real image, so we attach it to the exception and raise it so the caller can return it!
        image_call = illustration.latest_call("base_image_generator", "image_editor")
        if image_call and image_call.data.get("storage_ref"):
            exc.partial_storage_ref = image_call.data["storage_ref"]
        raise

    all_image_calls = [
        c for c in illustration.tool_calls 
        if c.tool_name in ("base_image_generator", "image_editor") and c.ok and c.data.get("storage_ref")
    ]
    
    if not all_image_calls:
        raise SpecialistFailed(
            "illustrator", "did not produce an image via base_image_generator (after one review retry)"
        )
        
    primary_call = all_image_calls[-1]
    storage_ref = primary_call.data["storage_ref"]
    image_prompt = illustration.get("image_prompt", "")
    
    # Add older generated options (if any) to extra elements so the user sees everything produced
    extra_images = []
    for call in all_image_calls[:-1]:
        extra_images.append({
            "storage_ref": call.data["storage_ref"],
            "element_type": "image",
            "produced_by_specialist": "illustrator",
            "metadata": {"tool_used": call.tool_name, "is_alternate_option": True},
        })

    # The real aspect_ratio actually sent to base_image_generator specifically (image_editor
    # doesn't change dimensions, so it never carries this arg) — what Format/Technical QA's
    # compliance check verifies the output against, not the LLM's own restated JSON field.
    generate_call = next((c for c in reversed(all_image_calls) if c.tool_name == "base_image_generator"), None)
    aspect_ratio = generate_call.args.get("aspect_ratio", "1:1") if generate_call else "1:1"

    # Real, live-found bug (2026-09-24, per an explicit user report: "most of the generations are
    # taking place perfectly but they are not being shown"): by this point Illustrator has ALREADY
    # produced a real image via `base_image_generator`/`image_editor` — a real, valid `storage_ref`.
    # Composition Artist here is a REFINEMENT pass over that already-complete image, not what
    # produces it — but with no try/except, any failure inside it (a provider outage, an unrelated
    # guardrail conflict — a real, live-reported case: "The request to add a price discount
    # conflicts with campaign constraints") propagated straight out of this function, all the way
    # to `graph.py`'s `except SpecialistFailed`, discarding the already-generated image entirely —
    # the user saw "ran into an issue, try again" with nothing to show for a real, already-complete
    # generation. Same real degrade `motion_lead.py` already uses when `video_editor_cutter` fails
    # after Camera Director's real paid render succeeds: a real, unrefined result beats a discarded
    # one every time.
    try:
        composition = await run_specialist_agentic(
            "composition_artist",
            context=(
                f"The generated image's storage_ref is: {storage_ref}\nIts prompt was:\n{image_prompt}\n\n"
                f"Aesthetic direction:\n{aesthetic_direction}\n\n"
                f"Palette direction:\n{palette.get('palette_direction', '')}"
            ),
        )
    except SpecialistFailed as exc:
        log.warning(
            "visual_design_lead_composition_failed_keeping_illustration",
            extra={"_extra_error": exc.message, "_extra_storage_ref": storage_ref},
        )
        composition = AgenticStepResult(specialist_name="composition_artist", model="", data={}, tool_calls=[])
    edit_result = composition.latest_result("image_editor")
    if edit_result and edit_result.get("storage_ref"):
        storage_ref = edit_result["storage_ref"]

    # Product subject fidelity is now handled in one pass by the illustrator above (passing
    # reference_storage_ref to base_image_generator when a product photo exists). Qwen generates
    # the scene with the product already composited — no separate PIL overlay step needed.

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
        extra_elements=[creative_brief_extra] + extra_images,
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
