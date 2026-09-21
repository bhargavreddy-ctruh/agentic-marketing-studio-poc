"""
Targeted regenerate — Architecture.md section 1c: "select one element..., ask for a redo;
invokes exactly that one specialist, not the whole Lead." Unlike comment resolution (which may
route to a DIFFERENT specialist based on what the comment is about), regenerate always targets the
SAME specialist that produced the element in the first place — that's the definition that
distinguishes it from a comment in the reference architecture.
"""
from __future__ import annotations

from ...core.exceptions import NotFoundError
from ...models.canvas_element import CanvasElementModel
from ...repositories.base import CanvasRepository, CanvasVersionRepository, SessionRepository
from .element_edit_service import run_specialist_edit
from .versioning_service import CanvasVersioningService


async def regenerate_element(
    *,
    canvas: CanvasRepository,
    versions: CanvasVersionRepository,
    sessions: SessionRepository,
    element_id: str,
    instruction: str | None,
) -> CanvasElementModel:
    element = await canvas.get_element(element_id)
    if element is None:
        raise NotFoundError("CanvasElement", element_id)
    session = await sessions.get(element.session_id)
    approval_mode = session.approval_mode if session else "auto"

    # A storage_ref means nothing to a text-only specialist — it never saw the pixels and has no
    # memory of its own prior run. Real bug found live (Memory.md, Phase 4): without the original
    # prompt/description, a free-tier model asked to "redo this image, make it green" produced an
    # entirely unrelated photo (a phone on marble, not the apple that was actually there) because
    # it had nothing describing what "this image" even was. The original generation prompt is the
    # only real grounding available, so it's always included.
    original_description = (
        element.metadata_json.get("image_prompt")
        or element.metadata_json.get("frame_prompt")
        or element.metadata_json.get("motion_prompt")
        or "(no original prompt was recorded for this element)"
    )
    context = (
        f"You previously produced this exact {element.element_type} with this prompt:\n"
        f"{original_description}\n\n"
        f"Its current storage_ref (pass this if you choose to edit it rather than regenerate from "
        f"scratch): {element.storage_ref}\n\n"
        f"Redo it, keeping everything the same EXCEPT the requested change below — this is a "
        f"targeted regenerate, not a free reinterpretation.\n"
        f"Requested change: {instruction or 'no specific change requested — regenerate as close to the original as possible'}"
    )
    new_storage_ref, tool_used, step = await run_specialist_edit(
        target_specialist=element.produced_by_specialist, context=context
    )

    versioning = CanvasVersioningService(canvas, versions)
    return await versioning.apply_or_stage(
        element,
        storage_ref=new_storage_ref,
        metadata={
            **element.metadata_json,
            "targeted_regenerate": True,
            "instruction": instruction,
            "tool_used": tool_used,
            **step.data,
        },
        action="regenerate",
        approval_mode=approval_mode,
    )
