"""
Shared "run one specialist against an existing element, pick up whatever real tool output it
produces" logic — used by targeted regeneration and comment resolution (Architecture.md section
1c's intervention paths). Generic across every specialist regardless of which tool it genuinely
chooses to call: a Composition Artist's `image_editor` result, a Lighting Designer's, a Camera
Director's `base_video_generator` result — all picked up the same way, since dynamic tool-calling
(Memory.md, Phase 2) means the caller can't know in advance which tool a specialist will use.
"""
from __future__ import annotations

from ...core.exceptions import SpecialistFailed
from ..specialists.runner import AgenticStepResult, run_specialist_agentic


async def run_specialist_edit(*, target_specialist: str, context: str) -> tuple[str, str, AgenticStepResult]:
    """Runs one specialist against the given context and returns (new_storage_ref, tool_used,
    the_full_step) for whichever tool call produced a real asset. Raises SpecialistFailed if the
    specialist ran but made no tool call that produced a storage_ref — an honest failure, not a
    fabricated result."""
    step = await run_specialist_agentic(target_specialist, context=context)
    for call in reversed(step.tool_calls):
        if call.ok and call.data.get("storage_ref"):
            return call.data["storage_ref"], call.tool_name, step
    raise SpecialistFailed(target_specialist, "did not produce a new asset via any tool call")
