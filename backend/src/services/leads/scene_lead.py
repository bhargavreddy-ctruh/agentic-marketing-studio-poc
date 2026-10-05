"""
Scene Lead — Architecture.md section 1a (collapsed, fidelity audit 2026-10-05): used to run
Environment Designer -> Prop Stylist -> Lighting Designer (generate, then two edit passes) as 3
genuinely serial LLM hops per shot. `scene_builder` now does the same job — environment, props,
and lighting decided together and baked into ONE generation call — since the extra two edit-pass
hops added real LLM cost per shot for comparatively thin creative value (confirmed: both were
1-2-sentence description passes with no independent visual-quality instructions of their own). The
three original specialists stay registered (harmless) but are no longer called from here.
"""
from __future__ import annotations

from ...core.exceptions import SpecialistFailed
from ..specialists.runner import run_specialist_with_review
from .base import LeadSpec, ScenePlan

SCENE_LEAD = LeadSpec(
    name="scene_lead",
    specialist_sequence=("scene_builder",),
    full_job_trigger="New scene/background",
)


async def run_scene_lead(*, shot_description: str, brief: dict | None = None) -> ScenePlan:
    # Same "generate or nothing valid" retry safety net environment_designer used to have —
    # empirically confirmed at ~1-in-3 on weaker models for this exact shape of requirement.
    scene = await run_specialist_with_review(
        "scene_builder",
        context=f"Shot to produce:\n{shot_description}",
        brief=brief,
        needs_retry=lambda r: not (
            r.get("use_existing_image_as_scene") or
            (r.latest_call("base_image_generator") and r.latest_call("base_image_generator").data.get("storage_ref"))
        ),
        reminder=(
            "REMINDER: you neither set 'use_existing_image_as_scene: true' nor called base_image_generator. "
            "You MUST do one or the other before responding with final JSON."
        ),
    )

    if scene.get("use_existing_image_as_scene"):
        reference_storage_ref = None
        if brief:
            reference_storage_ref = next(
                (
                    el["storage_ref"] for el in brief.get("referenced_elements_context", [])
                    if el.get("element_type") == "image" and el.get("storage_ref")
                ),
                None
            )
        if not reference_storage_ref:
            raise SpecialistFailed(
                "scene_builder", "chose to use existing image, but no image reference was found in brief"
            )
        storage_ref = reference_storage_ref
    else:
        image_result = scene.latest_result("base_image_generator")
        if not image_result or not image_result.get("storage_ref"):
            raise SpecialistFailed(
                "scene_builder", "did not produce an image via base_image_generator"
            )
        storage_ref = image_result["storage_ref"]

    scene_card_call = scene.latest_call("text_card_writer")
    scene_description_storage_ref = (
        scene_card_call.data.get("storage_ref") if scene_card_call else None
    )

    return ScenePlan(
        environment_description=scene.get("environment_description", ""),
        prop_description=scene.get("prop_description") or None,
        lighting_description=scene.get("lighting_description", ""),
        scene_image_storage_ref=storage_ref,
        scene_description_storage_ref=scene_description_storage_ref,
    )
