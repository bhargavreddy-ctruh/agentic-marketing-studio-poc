"""
LeadSpec — a Lead is just an ordered list of specialist names, transcribed directly from
Architecture.md section 1a. Changing a Lead's recipe means editing that one list, never touching
the Orchestrator (Rules.md section 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LeadSpec:
    name: str
    specialist_sequence: tuple[str, ...]  # run in this order; parallel groups noted in docstring
    full_job_trigger: str


@dataclass
class LeadResult:
    """
    A typed result crossing the Lead-executor -> orchestration-graph boundary (Rules.md section 2:
    no bare dict crossing a layer boundary) — the previous shape was a plain dict consumed via
    `.get(...)`, undocumented as an accepted exception unlike GraphState's LangGraph-mandated
    TypedDict. `to_dict()` exists solely to cross into GraphState at the graph-assembly boundary,
    which IS the documented, LangGraph-required exception.
    """

    storage_ref: str
    produced_by_specialist: str
    element_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # Real, distinct intermediate artifacts a Lead run genuinely produced along the way (a scene's
    # starting still frame, a raw pre-stitch video clip, a standalone voiceover track, an overlaid
    # still) — each already has a real storage_ref on disk (2026-09-22: previously these only
    # survived as a string buried in `metadata`, recoverable by a human reading JSON but never
    # visible on the canvas at all, even though the reference product this POC is modeled on shows
    # exactly this kind of artifact as its own tile). Deliberately does NOT include every
    # in-place-edit intermediate (e.g. composition_artist's edit of the illustrator's own image) —
    # those are the SAME logical asset revised, which `CanvasVersioningService` already models
    # correctly; sweeping every tool call generically would double-count those as fake siblings
    # instead of versions. Each entry: {storage_ref, element_type, produced_by_specialist, metadata}.
    extra_elements: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage_ref": self.storage_ref,
            "produced_by_specialist": self.produced_by_specialist,
            "element_type": self.element_type,
            "metadata": self.metadata,
            "extra_elements": self.extra_elements,
        }


@dataclass
class NarrativePlan:
    """Narrative Lead's output — a planning artifact, not a canvas asset, so it has no
    storage_ref/element_type (unlike LeadResult). Consumed by Motion Lead to enrich Camera
    Director's prompt (Architecture.md section 2.1: Narrative Lead -> Scene Lead -> Motion Lead).

    to_dict/from_dict exist so a plan can be staged in a session's JSON `brief` across turns —
    needed for real per-stage approval gates (Memory.md, Phase 4: "approve" mode pauses between
    Leads, so the already-produced plan must survive until the next turn resumes it)."""

    shots: tuple[str, ...]
    overall_story: str
    script_line: str | None
    pacing_target: str
    # The real `text_card_writer` result (2026-09-22) — Shot Planner's own genuine tool call
    # documenting the shot list, reused by Motion Lead for the real "shot_list" canvas card
    # instead of a Python-hand-built string. None only if the specialist genuinely skipped the
    # (required, but a real model can be flaky) tool call — callers fall back honestly in that case.
    shot_list_storage_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "shots": list(self.shots), "overall_story": self.overall_story,
            "script_line": self.script_line, "pacing_target": self.pacing_target,
            "shot_list_storage_ref": self.shot_list_storage_ref,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NarrativePlan:
        return cls(
            shots=tuple(data["shots"]), overall_story=data["overall_story"],
            script_line=data.get("script_line"), pacing_target=data["pacing_target"],
            shot_list_storage_ref=data.get("shot_list_storage_ref"),
        )


@dataclass
class ScenePlan:
    """Scene Lead's output — a planning artifact for one shot, consumed by Motion Lead the same
    way as NarrativePlan. `scene_image_storage_ref` is the real starting-frame image Environment
    Designer generated (and Prop Stylist/Lighting Designer may have edited) — Motion Lead animates
    this directly rather than generating its own starting frame (Memory.md, Phase 2 correction).

    to_dict/from_dict — same reason as NarrativePlan's: survive a real approval-gate pause."""

    environment_description: str
    prop_description: str | None
    lighting_description: str
    scene_image_storage_ref: str
    # The real `text_card_writer` result (2026-09-22) — Lighting Designer's own genuine tool call
    # documenting this scene, reused by Motion Lead for the real "scene_description" canvas card
    # instead of a Python-hand-built string. None only if the specialist genuinely skipped the
    # (required, but a real model can be flaky) tool call — callers fall back honestly in that case.
    scene_description_storage_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "environment_description": self.environment_description,
            "prop_description": self.prop_description,
            "lighting_description": self.lighting_description,
            "scene_image_storage_ref": self.scene_image_storage_ref,
            "scene_description_storage_ref": self.scene_description_storage_ref,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ScenePlan:
        return cls(
            environment_description=data["environment_description"],
            prop_description=data.get("prop_description"),
            lighting_description=data["lighting_description"],
            scene_image_storage_ref=data["scene_image_storage_ref"],
            scene_description_storage_ref=data.get("scene_description_storage_ref"),
        )


def referenced_element_block(brief: dict) -> str:
    """A real, explicit callout of whichever element the user actually referenced this turn
    (`session_service.py`'s `latest_element_*` scratch fields, resolved from a real
    `referenced_element_id` or the session's own most recent element) — added 2026-09-22, a real
    live-found gap: `_direct_fix_node` (graph.py) already surfaces this explicitly with "Act on
    THIS existing asset" framing, but every FRESH-generation Lead (`visual_design_lead.py`,
    `narrative_lead.py`, `motion_lead.py`) only ever had this buried inside a raw `json.dumps(brief)`
    dump alongside dozens of unrelated scratch keys — genuinely present, but not called out, the
    same "noise measurably biases the model" failure mode `ideation_service.py`'s own
    `_check_followup_clarity` fix already identified and fixed elsewhere, just never applied here.
    `motion_lead.py`'s Sound Designer had it missing ENTIRELY, not just buried — a referenced audio
    element's real content was invisible to it, so "same voiceover" could never actually work.
    Returns "" (safe to concatenate) when nothing was referenced this turn."""
    ref = brief.get("latest_element_storage_ref")
    if not ref:
        return ""
    kind = brief.get("latest_element_type", "element")
    desc = brief.get("latest_element_description") or "(no description recorded for it)"
    return (
        f"\n\nThe user is referencing an EXISTING {kind} already on the canvas (storage_ref: {ref}) "
        f"— its real content: {desc}\nUse this as real, direct grounding for what's being asked, "
        f"not the broader campaign context below."
    )


def stale_campaign_context_block(brief: dict) -> str:
    """`brief.idea` — added 2026-09-22, replacing every Lead's own ad-hoc inline version of this
    same string (previously duplicated near-verbatim in `visual_design_lead.py`, `narrative_lead.py`,
    `motion_lead.py`). A real, live-found leak this stronger framing exists to close: Ideation
    deliberately freezes `brief.idea` the instant a session's first element exists
    (`ideation_service.py`, to prevent an earlier numeric-erosion bug) — meaning for a long-lived
    session, this text can be HOURS or DAYS old and describe a completely different, already
    -abandoned concept (confirmed live: a real user session's `brief.idea` stayed "a modern
    minimalist logo" for the rest of that session's life, silently bleeding a "modern minimalist"
    framing into unrelated later requests — a Ferrari video, a concert poster — even though the
    current message was always the nominal "primary driver"). The old, softer "for supporting
    detail only" wording was too weak for weaker/free-tier models to reliably ignore. This version
    says explicitly it may be STALE and to ignore it outright unless the current request itself
    references it."""
    idea = brief.get("idea")
    if not idea:
        return ""
    return (
        f"\n\n(Earlier campaign notes from this session, which may be OLD and describe a "
        f"completely different, already-finished request — use this ONLY if the message above "
        f"itself clearly builds on it; otherwise ignore it completely: {idea})"
    )


# Real, deterministic keyword->aspect_ratio backstop (2026-09-25) — `illustrator.md`'s rule 3b
# already tells the model to set a real `aspect_ratio` for a poster/story/etc request, but that's
# 100% dependent on the LLM actually following it; under provider stress this codebase falls back
# to weaker models (Memory.md) that don't reliably apply prompt rules. Same "deterministic beats
# trusting an LLM" pattern already used elsewhere (`_is_bare_greeting`, `_check_price_stated`) —
# this backs up rule 3b, doesn't replace it. Checked in order; first match wins, so more specific
# phrases (an explicit "9:16") are listed before broader ones.
_ASPECT_RATIO_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("9:16",), "9:16"),
    (("story", "reel", "vertical"), "9:16"),
    (("16:9",), "16:9"),
    (("banner", "landscape"), "16:9"),
    (("poster",), "2:3"),
    (("1:1",), "1:1"),
    (("square", "instagram post"), "1:1"),
)


def infer_aspect_ratio_from_text(text: str) -> str | None:
    """Pure, deterministic — no LLM/network call. Returns the first matching aspect ratio for a
    real format keyword found in `text`, or None if nothing matched (caller injects nothing in
    that case, leaving the model's own judgment / the tool's own default in place)."""
    lowered = (text or "").lower()
    for keywords, ratio in _ASPECT_RATIO_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return ratio
    return None


def aspect_ratio_hint_block(text: str) -> str:
    """Safe-to-concatenate wrapper around `infer_aspect_ratio_from_text` — "" when nothing
    matched, otherwise an explicit, imperative instruction naming the real detected format."""
    ratio = infer_aspect_ratio_from_text(text)
    if not ratio:
        return ""
    return (
        f"\n\nDETECTED FORMAT: the request's wording matches a real, known format keyword — set "
        f"aspect_ratio to \"{ratio}\" on base_image_generator/image_editor unless the user's "
        f"wording explicitly asks for a different ratio."
    )
