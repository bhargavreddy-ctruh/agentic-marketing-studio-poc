"""
SPECIALIST_REGISTRY — specialists are declarative specs (data), not hand-written functions.

Adding specialist #15: add one SpecialistSpec entry below + one prompt file in
services/specialists/prompts/. No orchestrator, Lead, or other specialist file changes
(Rules.md section 1/2). The prompt text itself lives in its own file, not inline in Python, so a
prompt tweak is a content edit, not a code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core.exceptions import SpecialistNotFound
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier

log = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class SpecialistSpec:
    name: str
    prompt_file: str  # relative to services/specialists/prompts/
    allowed_tools: tuple[str, ...]
    tier: ModelTier

    def load_prompt(self) -> str:
        """The specialist's own prompt, with the shared security-boundary block appended —
        reused verbatim across every specialist (Rules.md section 6), written once here rather
        than copy-pasted into each prompt file."""
        path = _PROMPTS_DIR / self.prompt_file
        if not path.exists():
            raise SpecialistNotFound(f"{self.name} (missing prompt file {path.name})")
        boundary = (_PROMPTS_DIR / "_security_boundary.md").read_text(encoding="utf-8")
        return f"{path.read_text(encoding='utf-8')}\n\n{boundary}"


# Phase 0 scope: empty on purpose. Phase 1 adds the Visual Design Lead's four specs
# (Reference Curator, Palette Strategist, Illustrator, Composition Artist — Architecture.md
# section 1a), each with a corresponding .md file under prompts/. This list is the ONLY place a
# new specialist gets registered.
SPECIALIST_REGISTRY: dict[str, SpecialistSpec] = {}


def register_specialist(spec: SpecialistSpec) -> None:
    SPECIALIST_REGISTRY[spec.name] = spec
    log.info("specialist_registered", extra={"_extra_specialist": spec.name, "_extra_tier": spec.tier.name})


def get_specialist(name: str) -> SpecialistSpec:
    spec = SPECIALIST_REGISTRY.get(name)
    if spec is None:
        raise SpecialistNotFound(name)
    return spec


def load_all_specialists() -> None:
    """
    Register every specialist spec. Adding specialist #15 means one new SpecialistSpec entry
    here + one new prompt file — no orchestrator, Lead, or other specialist file changes
    (Rules.md section 1/2). Tiers below match the earlier full architecture document's
    per-specialist assignments exactly.
    """
    # Brand Kit Lookup is available to every specialist per the reference doc's own tool table
    # ("Brand Kit Lookup ... Used by: Every specialist below") — Phase 3 correction: it had only
    # been wired to Palette Strategist and Script Writer before (Memory.md, Phase 3).
    register_specialist(SpecialistSpec(
        name="reference_curator",
        prompt_file="reference_curator.md",
        allowed_tools=("web_trend_search", "asset_mood_board_search", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="palette_strategist",
        prompt_file="palette_strategist.md",
        allowed_tools=("color_palette_extractor", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="illustrator",
        # product_lookup added per the reference doc's tool table ("Product Lookup ... Used by:
        # Illustrator, Overlay Artist, Visual Fidelity Checker") — Illustrator never had it before.
        prompt_file="illustrator.md",
        allowed_tools=("base_image_generator", "image_editor", "brand_kit_lookup", "product_lookup"),
        tier=ModelTier.TIER_3,
    ))
    register_specialist(SpecialistSpec(
        name="composition_artist",
        prompt_file="composition_artist.md",
        allowed_tools=("image_editor", "brand_kit_lookup"),
        tier=ModelTier.TIER_2,
    ))
    register_specialist(SpecialistSpec(
        name="camera_director",
        prompt_file="camera_director.md",
        allowed_tools=("base_video_generator", "brand_kit_lookup"),
        tier=ModelTier.TIER_3,
    ))
    register_specialist(SpecialistSpec(
        name="video_editor_cutter",
        prompt_file="video_editor_cutter.md",
        allowed_tools=("video_stitcher", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="sound_designer",
        prompt_file="sound_designer.md",
        allowed_tools=("brand_kit_lookup", "text_to_speech", "mux_audio_into_video"),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="overlay_artist",
        prompt_file="overlay_artist.md",
        allowed_tools=("product_lookup", "discount_claims_calculator", "brand_kit_lookup", "text_overlay"),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="shot_planner",
        prompt_file="shot_planner.md",
        allowed_tools=("web_trend_search", "brand_kit_lookup"),
        tier=ModelTier.TIER_2,
    ))
    register_specialist(SpecialistSpec(
        name="script_writer",
        prompt_file="script_writer.md",
        allowed_tools=("brand_kit_lookup",),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        # Deliberately NOT given brand_kit_lookup, unlike every other specialist — the reference
        # doc explicitly calls this one out as "reasoning only, no tools" (Memory.md, Phase 3
        # conformance audit: the blanket "give every specialist brand access" fix wrongly included
        # this one; reverted).
        name="pacing_editor",
        prompt_file="pacing_editor.md",
        allowed_tools=(),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="environment_designer",
        prompt_file="environment_designer.md",
        allowed_tools=("base_image_generator", "brand_kit_lookup"),
        tier=ModelTier.TIER_2,
    ))
    register_specialist(SpecialistSpec(
        name="prop_stylist",
        prompt_file="prop_stylist.md",
        allowed_tools=("image_editor", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
    ))
    register_specialist(SpecialistSpec(
        name="lighting_designer",
        prompt_file="lighting_designer.md",
        allowed_tools=("image_editor", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
    ))
    log.info("specialists_loaded", extra={"_extra_count": len(SPECIALIST_REGISTRY)})
