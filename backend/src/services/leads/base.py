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


# Real, live-found performance + precision bug (2026-10-05, fidelity audit): a canvas element's
# `description` can be a multi-hundred-word vision-model essay (`session_service.py`'s
# `_describe_uploaded_image`) — passed verbatim, uncapped, into every specialist's context. Reuses
# the same bound `canvas_mapper.py`'s own list-response truncation already applies, so a long
# description never dominates a specialist's limited context budget.
_MAX_INTERPOLATED_DESCRIPTION_CHARS = 300


def _truncate_description(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_INTERPOLATED_DESCRIPTION_CHARS:
        return text
    return text[:_MAX_INTERPOLATED_DESCRIPTION_CHARS].rstrip() + "…"


def available_context_block(brief: dict) -> str:
    """A short, STRUCTURED "what's actually available this turn" summary — replaces the raw
    `json.dumps(brief)` tail every Lead used to hand a specialist (`visual_design_lead.py`,
    `narrative_lead.py`, `motion_lead.py`, `graph.py`'s direct_fix path). Real, live-found gap this
    closes (2026-10-05, explicit user ask: "every llm should know what parameters/images/everything
    they have... without filling useless/bloating the llm with unnecessary data"): `brief` typically
    holds 20+ keys — campaign/product/brand detail blobs, narrative/scene plans, recent chat
    history, internal scratch flags (`_error`, `_resume_*`, `_element_disambiguation_needed`) — none
    of it curated for the receiving specialist. Dumping all of it doesn't make more real facts
    available; it just buries the handful that actually matter under noise the model has to sift
    through itself — the same "noise measurably biases the model" failure mode
    `referenced_element_block`'s own docstring below already names, just not previously applied to
    the brief as a whole. States plainly what's resolved and which tool reveals more — never the
    raw values themselves — so this stays short regardless of how much is actually in the brief.
    Deliberately excludes every internal/scratch key; a specialist never needs to know about
    pause/resume bookkeeping or disambiguation flags to do its own job."""
    lines = ["Available context for this request (use the named tool to see the real details):"]

    product_id = brief.get("resolved_product_id")
    if product_id:
        lines.append(
            f"- A product is linked (id: {product_id}) — call `product_lookup` for its real "
            f"must_show/never_show/claims/price facts before using any of them."
        )
    else:
        lines.append("- No specific product is resolved for this request.")

    refs = brief.get("referenced_elements_context") or []
    if refs:
        for el in refs[:3]:  # a bounded preview, not every referenced element's full description
            kind = el.get("element_type", "element")
            desc = el.get("description")
            desc_part = f" — {_truncate_description(desc)}" if isinstance(desc, str) and desc.strip() else ""
            lines.append(f"- Referenced {kind} (storage_ref: {el.get('storage_ref')}){desc_part}")
        if len(refs) > 3:
            lines.append(f"- ...and {len(refs) - 3} more referenced element(s).")
    else:
        lines.append("- No elements are explicitly referenced this turn.")

    deliverable = brief.get("deliverable")
    if deliverable:
        lines.append(f"- Target deliverable/format: {deliverable}.")

    if brief.get("approved_script"):
        lines.append("- A script was already approved earlier this session — reuse it, don't rewrite it.")

    # Ledger + Digests (2026-10-05, tiered conversation memory) — local import to avoid a circular
    # import (`conversation_memory.py` is a sibling module, not a dependency of this one at load
    # time). Both render to "" when absent (a fresh session, or memory not yet computed for this
    # turn), so this is a no-op addition for any brief that doesn't carry them.
    from ..knowledge.conversation_memory import digests_block, ledger_block

    ledger_text = ledger_block(brief.get("ledger"))
    if ledger_text:
        lines.append(ledger_text)
    digests_text = digests_block(brief.get("memory_digests"))
    if digests_text:
        lines.append(digests_text)

    return "\n\n" + "\n".join(lines)


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
    raw_desc = brief.get("latest_element_description")
    desc = _truncate_description(raw_desc) if isinstance(raw_desc, str) and raw_desc.strip() else "(no description recorded for it)"
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


def infer_aspect_ratio_from_text(text: str) -> str | None:
    """Pure, deterministic — no LLM/network call. Returns the first matching aspect ratio for a
    real format keyword found in `text`, or None if nothing matched (caller injects nothing in
    that case, leaving the model's own judgment / the tool's own default in place)."""
    from ...core.deliverables import detect_deliverable
    spec = detect_deliverable(text)
    return spec.aspect_ratio if spec else None


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
