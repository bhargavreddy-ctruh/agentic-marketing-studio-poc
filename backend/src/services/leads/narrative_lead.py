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
"""
from __future__ import annotations

import asyncio
import json

from ...core.exceptions import SpecialistFailed
from ..specialists.runner import run_specialist_agentic
from .base import LeadSpec, NarrativePlan

NARRATIVE_LEAD = LeadSpec(
    name="narrative_lead",
    specialist_sequence=("shot_planner", "script_writer", "pacing_editor"),
    full_job_trigger="New video storyboard",
)


async def run_narrative_lead(*, brief: dict) -> NarrativePlan:
    idea = brief.get("idea") or brief.get("initial_message") or ""

    planning = await run_specialist_agentic(
        "shot_planner", context=f"Campaign idea:\n{idea}\n\nBrief so far:\n{json.dumps(brief)}"
    )
    shots = tuple(s for s in (planning.get("shots") or []) if isinstance(s, str) and s.strip())
    if not shots:
        raise SpecialistFailed("shot_planner", "did not produce any shots")
    overall_story = planning.get("overall_story", "")

    script, pacing = await asyncio.gather(
        run_specialist_agentic(
            "script_writer",
            context=f"Shot list:\n{json.dumps(shots)}\n\nOverall story:\n{overall_story}",
        ),
        run_specialist_agentic("pacing_editor", context=f"Shot list:\n{json.dumps(shots)}"),
    )
    script_line = script.get("script_line") if script.get("has_script") else None

    return NarrativePlan(
        shots=shots,
        overall_story=overall_story,
        script_line=script_line,
        pacing_target=pacing.get("pacing_target", "moderate"),
    )
