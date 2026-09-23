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
    # A real, live-found gap (2026-09-21): `describe_specialists()` used to show the orchestrator's
    # routing classifier ONLY a name + tool list — e.g. "video_editor_cutter: tools =
    # video_stitcher, brand_kit_lookup" — with no indication of what the specialist actually DOES.
    # A request to genuinely CREATE a new video from an existing image ("make a video of it racing
    # on track") got misrouted to `direct_fix -> video_editor_cutter`, whose real job is only
    # assembling clips that ALREADY EXIST — it has no way to generate new footage at all, so it
    # silently stitched together whatever was already on hand instead. One line here, read by both
    # `orchestrator.py`'s route classifier and `specialist_classifier.py`'s target picker (Rules.md
    # section 1: DRY), is enough to disambiguate this reliably.
    description: str = ""
    # Opt-in, not opt-out: real, live-found regressions (2026-09-21, Memory.md) showed the
    # self-hosted TIER_1 model failing badly on every task tested EXCEPT Reference Curator's
    # (gathering references has no hard constraint to violate) — Overlay Artist specifically
    # fabricated a $250,000 price with zero grounding, directly disobeying its own prompt's
    # explicit "never invent a price" guardrail. Defaults to False (Groq-first, the same reliable
    # path every other tier already uses) until a specialist is individually verified safe the
    # same rigorous way Reference Curator was — never assumed safe by category alone.
    prefer_local: bool = False

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
        description="Gathers mood/trend references BEFORE any generation — never produces or edits an asset itself.",
        # The one specialist actually verified live against the local model (Memory.md,
        # 2026-09-21): real tool calls, real results, correct final reasoning — not assumed safe.
        prefer_local=True,
    ))
    register_specialist(SpecialistSpec(
        name="palette_strategist",
        prompt_file="palette_strategist.md",
        allowed_tools=("color_palette_extractor", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
        description="Decides a color direction BEFORE generation — never produces or edits an asset itself.",
        # Verified live against the local model (2026-09-21, 3 real runs: no brand kit, a real
        # brand_kit_lookup check, a nonexistent reference image) — correctly called tools only
        # when relevant, never fabricated a brand identity/price, always returned valid final
        # JSON. One real, disclosed downside: it retries a failing color_palette_extractor 3x
        # before giving up when the referenced image doesn't actually exist, instead of noticing
        # sooner — a latency cost, not a correctness one, so kept enabled.
        prefer_local=True,
    ))
    register_specialist(SpecialistSpec(
        name="illustrator",
        # product_lookup added per the reference doc's tool table ("Product Lookup ... Used by:
        # Illustrator, Overlay Artist, Visual Fidelity Checker") — Illustrator never had it before.
        prompt_file="illustrator.md",
        allowed_tools=("base_image_generator", "image_editor", "brand_kit_lookup", "product_lookup"),
        tier=ModelTier.TIER_3,
        description="Generates the base still IMAGE from scratch (full_image pipeline's core step).",
    ))
    register_specialist(SpecialistSpec(
        name="composition_artist",
        prompt_file="composition_artist.md",
        # text_card_writer added 2026-09-22 — writes the real, visible "creative brief" text card
        # (aesthetic/palette direction + the real image prompt used) as a genuine tool call, not
        # Python code hand-building a string after the fact (Rules.md section 1: the same agentic
        # pattern every other generation capability already uses).
        allowed_tools=("image_editor", "brand_kit_lookup", "text_card_writer"),
        tier=ModelTier.TIER_2,
        description="Applies a targeted EDIT to an already-existing image using the image_editor tool (direct_fix use case). Handles ALL visual modifications to images: recoloring, changing subjects, editing content, striking through prices, adding discounted prices visually onto an image, modifying text baked into images, and any request that says 'image edit' or 'edit image'. This is the DEFAULT specialist for any image edit request. Never generates a new image from scratch, never touches video. Also writes the real creative-brief text card for a just-generated image.",
    ))
    register_specialist(SpecialistSpec(
        name="camera_director",
        prompt_file="camera_director.md",
        allowed_tools=("base_video_generator", "brand_kit_lookup"),
        tier=ModelTier.TIER_3,
        description="Generates NEW raw video footage/clips from scratch (full_video pipeline's Motion Lead step) — this is the specialist for 'make a video of X', not video_editor_cutter.",
    ))
    register_specialist(SpecialistSpec(
        name="video_editor_cutter",
        prompt_file="video_editor_cutter.md",
        allowed_tools=("video_stitcher", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
        description=(
            "Assembles/cuts clip(s) that ALREADY EXIST into a final cut — it cannot generate any "
            "new footage at all. Never route a request to CREATE a new video here; that always "
            "needs camera_director (full_video pipeline) instead."
        ),
    ))
    register_specialist(SpecialistSpec(
        name="sound_designer",
        prompt_file="sound_designer.md",
        # mux_audio_into_video is no longer in this list (Tasks.md #1, 2026-09-22): muxing is now
        # performed directly by motion_lead.py's executor, a deterministic ffmpeg step with no
        # agentic reasoning at call time — sound_designer only decides WHETHER via its own
        # `should_mux` judgment, made without ever seeing the video (real parallel execution).
        allowed_tools=("brand_kit_lookup", "text_to_speech"),
        tier=ModelTier.TIER_1,
        description="Produces voiceover/narration audio (text_to_speech) independently of the video itself — decides whether it should later be muxed in, but never touches video directly.",
    ))
    register_specialist(SpecialistSpec(
        name="overlay_artist",
        prompt_file="overlay_artist.md",
        allowed_tools=(
            "product_lookup", "discount_claims_calculator", "discount_math_calculator",
            "brand_kit_lookup", "text_overlay",
        ),
        tier=ModelTier.TIER_1,
        description="Adds a SIMPLE text label or headline onto an image as a floating overlay (NOT baked into the image). ONLY use when the user explicitly asks to 'add text', 'add a headline', or 'add a caption' as a separate layer. Do NOT use for image edits, price modifications, striking through prices, or any visual modification of the image content itself — those go to composition_artist.",
    ))
    register_specialist(SpecialistSpec(
        name="shot_planner",
        prompt_file="shot_planner.md",
        # text_card_writer added 2026-09-22 — writes the real, visible "shot_list" text card as a
        # genuine tool call, the same agentic pattern every other generation capability already
        # uses, per an explicit user ask.
        allowed_tools=("web_trend_search", "brand_kit_lookup", "text_card_writer"),
        tier=ModelTier.TIER_2,
        description="Plans shot composition for the full_video pipeline BEFORE any footage is generated — never produces or edits a media asset itself, but does write the real shot-list text card.",
    ))
    register_specialist(SpecialistSpec(
        name="script_writer",
        prompt_file="script_writer.md",
        allowed_tools=("brand_kit_lookup",),
        tier=ModelTier.TIER_1,
        description="Writes the narration/voiceover SCRIPT TEXT for the full_video pipeline — text only, produces no audio or video itself (sound_designer turns this into real audio).",
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
        description="Reasoning-only pacing feedback for the full_video pipeline — no tools, produces no asset.",
    ))
    register_specialist(SpecialistSpec(
        name="environment_designer",
        prompt_file="environment_designer.md",
        allowed_tools=("base_image_generator", "brand_kit_lookup"),
        tier=ModelTier.TIER_2,
        description="Generates a background/scene IMAGE from scratch for the full_video pipeline's Scene Lead step.",
    ))
    register_specialist(SpecialistSpec(
        name="prop_stylist",
        prompt_file="prop_stylist.md",
        allowed_tools=("image_editor", "brand_kit_lookup"),
        tier=ModelTier.TIER_1,
        description="Applies a targeted prop/detail EDIT to an already-existing image (e.g., recoloring, adding/removing objects) — never generates a new image from scratch.",
    ))
    register_specialist(SpecialistSpec(
        name="lighting_designer",
        prompt_file="lighting_designer.md",
        # text_card_writer added 2026-09-22 — writes the real, visible "scene_description" text
        # card (environment + props + lighting) as a genuine tool call, the same agentic pattern
        # every other generation capability already uses, per an explicit user ask.
        allowed_tools=("image_editor", "brand_kit_lookup", "text_card_writer"),
        tier=ModelTier.TIER_1,
        description="Applies a targeted lighting EDIT to an already-existing image — never generates a new image from scratch. Also writes the real scene-description text card for the scene.",
    ))
    register_specialist(SpecialistSpec(
        # Added 2026-09-22, per an explicit user ask: text (a description of an existing element,
        # a narrative, a summary) should be reachable as a real, modular capability like any other
        # — not something only auto-attached inside the full_image/full_video pipelines. Reachable
        # via direct_fix whenever a request is genuinely "describe/narrate/summarize this in
        # writing" rather than "generate/edit an image/video/audio" — never generates or edits a
        # media asset itself, text is its entire job.
        name="narrator",
        prompt_file="narrator.md",
        # audio_transcriber added 2026-09-22 — real understanding of an existing AUDIO element
        # (words, pace, acoustic mood) when asked to describe one, not just a guess from its label.
        allowed_tools=("text_card_writer", "brand_kit_lookup", "audio_transcriber"),
        tier=ModelTier.TIER_2,
        description="Writes a real text card describing an existing image/video/audio element, or a narrative/summary in writing — never generates or edits an image/video/audio asset itself. The right target whenever the user asks for something to be DESCRIBED, narrated, or summarized in text, not generated/edited as a new media asset.",
    ))
    log.info("specialists_loaded", extra={"_extra_count": len(SPECIALIST_REGISTRY)})
