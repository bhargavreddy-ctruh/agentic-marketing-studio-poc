"""
Scene Lead — Architecture.md section 1a: Environment Designer -> Prop Stylist -> Lighting
Designer, in that order (Prop Stylist and Lighting Designer both react to the image the previous
step actually produced, so this sequence is genuinely serial).

Environment Designer genuinely generates the scene's starting frame itself — per the reference
architecture's own tool-ownership table (Environment Designer: Base Image Generator; Prop
Stylist/Lighting Designer: Image Editor/Inpainter). Earlier this session, Camera Director stood in
for this job because Scene Lead didn't exist yet; now that it does, that stand-in is retired
(Memory.md, Phase 2). Prop Stylist and Lighting Designer each decide for themselves, via real
tool-calling, whether a targeted image_editor pass on that image is genuinely needed.
"""
from __future__ import annotations

from ...core.exceptions import SpecialistFailed
from ..specialists.runner import run_specialist_agentic
from .base import LeadSpec, ScenePlan

SCENE_LEAD = LeadSpec(
    name="scene_lead",
    specialist_sequence=("environment_designer", "prop_stylist", "lighting_designer"),
    full_job_trigger="New scene/background",
)


async def run_scene_lead(*, shot_description: str) -> ScenePlan:
    environment = await run_specialist_agentic(
        "environment_designer", context=f"Shot to produce:\n{shot_description}"
    )
    image_result = environment.latest_result("base_image_generator")
    if not image_result or not image_result.get("storage_ref"):
        raise SpecialistFailed(
            "environment_designer", "did not produce an image via base_image_generator"
        )
    storage_ref = image_result["storage_ref"]
    environment_description = environment.get("environment_description", "")

    props = await run_specialist_agentic(
        "prop_stylist",
        context=(
            f"Shot to produce:\n{shot_description}\n\nEnvironment:\n{environment_description}\n\n"
            f"Current image storage_ref: {storage_ref}"
        ),
    )
    prop_edit = props.latest_result("image_editor")
    if prop_edit and prop_edit.get("storage_ref"):
        storage_ref = prop_edit["storage_ref"]
    prop_description = props.get("prop_description") if props.get("has_props") else None

    lighting = await run_specialist_agentic(
        "lighting_designer",
        context=(
            f"Shot to produce:\n{shot_description}\n\nEnvironment:\n{environment_description}\n\n"
            f"Current image storage_ref: {storage_ref}"
        ),
    )
    lighting_edit = lighting.latest_result("image_editor")
    if lighting_edit and lighting_edit.get("storage_ref"):
        storage_ref = lighting_edit["storage_ref"]

    return ScenePlan(
        environment_description=environment_description,
        prop_description=prop_description,
        lighting_description=lighting.get("lighting_description", ""),
        scene_image_storage_ref=storage_ref,
    )
