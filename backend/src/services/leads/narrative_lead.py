"""
Narrative Lead — Architecture.md section 1a: Shot Planner -> Script Writer -> Pacing Editor.

Shot Planner must run first (both later steps need its shot list). Script Writer and Pacing Editor
are then genuinely independent of EACH OTHER — both only need the shot list, neither reads the
other's output — so they run concurrently (Memory.md, Phase 3 conformance audit: this was
previously run sequentially despite the earlier docstring's reasoning not actually holding up;
Architecture.md's own example is exactly this shape — parallelize specialists with no dependency).

Runs first in the full-video pipeline (Architecture.md section 2.1: Narrative Lead -> Scene Lead
-> Motion Lead) and hands its plan downstream — it doesn't produce a canvas asset itself, so it
returns a NarrativePlan, not a LeadResult.

Routed through `run_concurrent_specialists` (Tasks.md #4, 2026-09-22) — a real, live-found gap on
independent review: this file's own `asyncio.gather` had the exact same missing
`return_exceptions=True` that Motion Lead's did before its Task 1 fix. Pacing Editor failing used
to silently discard Script Writer's already-computed real result too, aborting Narrative Lead (and
therefore the whole video pipeline) over a specialist whose own result already has a sensible
default (`pacing_target="moderate"`, unchanged below).
"""
from __future__ import annotations

import json

from ...core.exceptions import SpecialistFailed
from ..specialists.runner import run_concurrent_specialists, run_specialist_agentic
from .base import LeadSpec, NarrativePlan, referenced_element_block, stale_campaign_context_block

NARRATIVE_LEAD = LeadSpec(
    name="narrative_lead",
    specialist_sequence=("shot_planner", "script_writer", "pacing_editor"),
    full_job_trigger="New video storyboard",
)


async def run_narrative_lead(*, brief: dict, user_message: str = "") -> NarrativePlan:
    """`user_message` (2026-09-22) — same real, live-found bug/fix as `visual_design_lead.py`'s own:
    `brief.idea` stays frozen once Ideation skips re-merging it for a session with any existing
    element, so a genuinely new video request needs its own literal text as the primary driver, not
    whatever old idea the brief happened to hold. See that file's docstring for the full story."""
    if user_message.strip():
        idea = user_message.strip() + stale_campaign_context_block(brief)
    else:
        idea = brief.get("idea") or brief.get("initial_message") or ""

    planning = await run_specialist_agentic(
        "shot_planner",
        context=f"Campaign idea:\n{idea}{referenced_element_block(brief)}\n\nBrief so far:\n{json.dumps(brief)}",
    )
    shots = tuple(s for s in (planning.get("shots") or []) if isinstance(s, str) and s.strip())
    if not shots:
        raise SpecialistFailed("shot_planner", "did not produce any shots")
    overall_story = planning.get("overall_story", "")

    results = await run_concurrent_specialists({
        "script_writer": run_specialist_agentic(
            "script_writer",
            context=f"Shot list:\n{json.dumps(shots)}\n\nOverall story:\n{overall_story}",
        ),
        "pacing_editor": run_specialist_agentic("pacing_editor", context=f"Shot list:\n{json.dumps(shots)}"),
    })
    script, pacing = results["script_writer"], results["pacing_editor"]
    script_line = script.get("script_line") if script.get("has_script") else None

    # The real `text_card_writer` call Shot Planner's own prompt now requires (2026-09-22) — None
    # only if the specialist genuinely skipped it; `motion_lead.py` falls back honestly.
    shot_list_call = planning.latest_call("text_card_writer")
    shot_list_storage_ref = shot_list_call.data.get("storage_ref") if shot_list_call else None

    return NarrativePlan(
        shots=shots,
        overall_story=overall_story,
        script_line=script_line,
        pacing_target=pacing.get("pacing_target", "moderate"),
        shot_list_storage_ref=shot_list_storage_ref,
    )
