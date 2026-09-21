"""
Comment resolution — Architecture.md section 1c: "a note on an element... that the orchestrator
resolves as a scoped instruction against that specific specialist." Unlike targeted regenerate
(always the same specialist that produced the element), a comment's real intent may point at a
DIFFERENT specialist than the one that originally produced the element (e.g. a comment on an
Illustrator-produced image about lighting genuinely belongs to Lighting Designer's job, not
Illustrator's) — so this classifies the target fresh from the comment text itself.
"""
from __future__ import annotations

from ...core.exceptions import NotFoundError, SpecialistFailed
from ...models.canvas_element import CanvasElementModel
from ...repositories.base import CanvasRepository, CanvasVersionRepository, SessionRepository
from ..orchestration.specialist_classifier import classify_target_specialist
from .element_edit_service import run_specialist_edit
from .versioning_service import CanvasVersioningService


async def resolve_comment(
    *,
    canvas: CanvasRepository,
    versions: CanvasVersionRepository,
    sessions: SessionRepository,
    element_id: str,
    comment: str,
) -> CanvasElementModel:
    element = await canvas.get_element(element_id)
    if element is None:
        raise NotFoundError("CanvasElement", element_id)
    session = await sessions.get(element.session_id)
    approval_mode = session.approval_mode if session else "auto"

    # A storage_ref means nothing to a text-only specialist — the original generation prompt is
    # the only real grounding for what this element actually depicts (same real bug found live in
    # regenerate_service.py, Memory.md Phase 4: without it, a free-tier model invents an unrelated
    # image rather than genuinely acting on the existing one).
    original_description = (
        element.metadata_json.get("image_prompt")
        or element.metadata_json.get("frame_prompt")
        or element.metadata_json.get("motion_prompt")
        or "(no original prompt was recorded for this element)"
    )

    target = await classify_target_specialist(
        comment,
        extra_context=(
            f"This comment is about an existing {element.element_type}, originally produced by "
            f"{element.produced_by_specialist}. It depicts:\n{original_description}"
        ),
    )
    if target is None:
        raise SpecialistFailed(
            "comment_resolution", "could not determine which specialist this comment belongs to"
        )

    context = (
        f"You are looking at an existing {element.element_type} that depicts:\n{original_description}\n\n"
        f"Its storage_ref (pass this to whatever tool you call on it): {element.storage_ref}\n\n"
        f"A user explicitly submitted this comment requesting a fix — this is real, deliberate "
        f"feedback, not a passing remark, so you should apply a real fix using your tool unless "
        f"the request is genuinely nonsensical for your role:\n\"{comment}\"\n"
        f"Resolve it into a concrete fix, keeping everything else about the original the same."
    )
    new_storage_ref, tool_used, step = await run_specialist_edit(target_specialist=target, context=context)

    versioning = CanvasVersioningService(canvas, versions)
    return await versioning.apply_or_stage(
        element,
        storage_ref=new_storage_ref,
        metadata={
            **element.metadata_json,
            "comment_resolved": True,
            "comment": comment,
            "resolved_by_specialist": target,
            "tool_used": tool_used,
            **step.data,
        },
        action="comment",
        approval_mode=approval_mode,
    )
