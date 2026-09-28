"""
LangGraph graph assembly — wires the Ideation node, the Orchestrator, and the Lead subgraphs
together. This is the ONLY file that calls StateGraph/compile — everything else just provides node
functions.

Multi-turn design (Architecture.md section 1d's loop): rather than pausing the graph mid-run
(LangGraph's interrupt() machinery), each turn re-invokes the WHOLE graph fresh with the
session's accumulated `brief` plus the latest user input — the same "client/session carries state
forward" pattern the existing agentic_flow codebase already uses successfully. If Ideation isn't
ready yet, the graph ends right there with proposed options; otherwise it falls through to the
Orchestrator and a real Lead. Simpler than checkpointed interrupts for a POC (Rules.md KISS), and
avoids new infrastructure to hold a graph mid-flight.

Phase 1 scope: the "full_image" route runs the real Visual Design Lead. Phase 2 adds the "full_video"
route running the real Narrative Lead -> Scene Lead -> Motion Lead pipeline (Architecture.md section
2.1); only the first shot Narrative Lead proposes is actually rendered per run, a deliberate
cost-control choice since video generation is pay-per-use — see motion_lead.py's own docstring.
"direct_fix" is now real too (Memory.md, Phase 3 conformance audit) — it had been a hardcoded
placeholder despite PRD.md listing it as in-scope; see `_direct_fix_node` below.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from ...core.approval import is_approval, is_cancel
from ...core.config import settings
from ...core.element_descriptions import NO_DESCRIPTION_SENTINEL
from ...core.events import emit
from ...core.exceptions import SpecialistFailed, SpecialistNotFound
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..ideation.ideation_service import run_ideation
from ..leads.base import (
    LeadResult,
    NarrativePlan,
    ScenePlan,
    referenced_element_block,
    stale_campaign_context_block,
)
from ..leads.motion_lead import run_motion_lead
from ..leads.narrative_lead import run_narrative_lead
from ..leads.scene_lead import run_scene_lead
from ..leads.visual_design_lead import run_visual_design_lead
from ..specialists.runner import run_concurrent_specialists, run_specialist_with_review
from .orchestrator import route, route_condition
from .state import GraphState

log = get_logger(__name__)

# Which real element_type each tool's own storage_ref output actually is — used by
# `_direct_fix_node` below to label a result by what it genuinely produced, not by guessing from
# whatever the previously-existing element happened to be (see that function's own comment for the
# real bug this fixes: a video onto a previously-image element used to keep `element_type: "image"`
# and get rendered as a broken `<img>` tag). Every tool that can set a top-level `storage_ref` in
# its `ToolResult.data` needs an entry here — `asset_mood_board_search`/`color_palette_extractor`
# don't (checked directly): the first nests refs inside a results list, the second only takes one
# as an input arg and never returns one.
_ELEMENT_TYPE_BY_TOOL = {
    "base_image_generator": "image",
    "image_editor": "image",
    "text_overlay": "image",
    "base_video_generator": "video",
    "video_stitcher": "video",
    # The registered tool name is "mux_audio_into_video" (see @register_tool in
    # audio_video_muxer.py) — "audio_video_muxer" is only that file's module name. Caught on
    # independent review (2026-09-22): the wrong key here silently defeated this map's whole
    # purpose for any direct_fix a muxer actually produced, falling through to the old buggy guess.
    "mux_audio_into_video": "video",
    "text_to_speech": "audio",
    "text_card_writer": "text",
}

# Tools that ANNOTATE an existing element rather than REPLACING its own content (2026-09-22) — a
# real, live-found-before-shipping correctness issue: `_direct_fix_node`'s default assumption is
# that a direct_fix's result IS the target element's new content (versioned in place — correct for
# e.g. video_editor_cutter turning an image element into a video, or image_editor recoloring it).
# `text_card_writer` is different in kind: "describe this image" must produce a NEW, separate text
# card sitting next to the image, never overwrite the image's own storage_ref with text (which
# would corrupt/destroy it). Checked before deciding `update_existing_element_id` below.
_ANNOTATION_ONLY_TOOLS = {"text_card_writer"}

# Parallel dynamic-plan dispatch safety guard (2026-09-26, parallel-dispatch investigation): the
# orchestrator's own `parallel_group` claim on a dynamic plan step is verified here before ever
# being trusted, not blindly acted on — same "deterministic beats a maybe" principle already used
# elsewhere in this codebase. Specialists that EDIT an existing asset (via image_editor/
# text_overlay) are never allowed into a concurrent group — an editing tool operates on a specific
# existing asset, and grouping it risks a race against a sibling step's own not-yet-produced
# output. Only genuinely independent FRESH-generation steps are safe to run concurrently —
# matches the same real, audited pattern `motion_lead.py`'s `run_concurrent_specialists` usage
# already proved out (independent GENERATION branches, never independent EDITS of the same thing).
_ASSET_MUTATING_SPECIALISTS = frozenset(
    {"composition_artist", "prop_stylist", "lighting_designer", "overlay_artist"}
)


def _produced_ref(step) -> tuple[str | None, str | None]:
    """Which tool call actually produced this step's result — a real, live-found bug (2026-09-23):
    the original version of this (duplicated identically in `_direct_fix_node` and
    `_dynamic_executor_node`) picked whichever tool call happened LAST in the agentic loop,
    tool-agnostic. `composition_artist` legitimately calls `image_editor` (the real edit) and THEN
    ALSO calls `text_card_writer` (a real, expected creative-brief card, per its own prompt) in the
    same turn — "last call wins" silently discarded the real edit and replaced it with the text
    card's own storage_ref instead, which downstream code (`_ANNOTATION_ONLY_TOOLS`'s OWN check,
    just one step later) then correctly refuses to apply to the existing element — but by then the
    real edit result is already gone, and a stray new TEXT element gets created in its place. A
    live-reported real turn showed exactly this: the referenced image and the actually-produced
    element were "totally different" — one was the real edited image (discarded), the other was
    the fallback from the wrong tool call.

    Fixed by preferring a REAL generation/edit result over an annotation-only side effect
    regardless of call order — reusing the `_ANNOTATION_ONLY_TOOLS` distinction the code already
    had, just applied at the point it actually matters. A specialist whose ENTIRE turn was
    genuinely annotation-only (e.g. `narrator`, which only ever calls `text_card_writer`) still
    gets that real result — this only changes the choice when a REAL result also exists."""
    calls_with_ref = [c for c in reversed(step.tool_calls) if c.ok and c.data.get("storage_ref")]
    real_result = next((c for c in calls_with_ref if c.tool_name not in _ANNOTATION_ONLY_TOOLS), None)
    chosen = real_result or (calls_with_ref[0] if calls_with_ref else None)
    return (chosen.data["storage_ref"], chosen.tool_name) if chosen else (None, None)


# Multi-generation (2026-09-22) — a single request can genuinely ask for several DISTINCT
# images/videos ("2 images, one pink one green"; "a car shot, then a video based on it"). Shared
# across both `_visual_design_lead_node` and `_motion_lead_node` (medium-agnostic: the planning
# call and the sequential/parallel execution logic don't care whether the thing being produced is
# an image or a video — only the per-variant "how do I actually run one" callable differs, which
# each caller supplies). A real, hard cap per medium — video especially, since each independent
# variant is its own separate PAID Replicate render; a wrong "count: 10" from a flaky free-tier
# classification call must never translate into 10 real paid renders.
_MAX_MULTI_IMAGE_COUNT = 4
_MAX_MULTI_VIDEO_COUNT = 2

_MULTI_GENERATION_PLAN_PROMPT = """You are deciding how many DISTINCT {medium}s a request is
actually asking for, and whether they are independent or dependent on each other.

Most requests ask for exactly ONE {medium} — default to count=1 unless the message CLEARLY asks
for multiple DISTINCT {medium}s (e.g. "generate 2 images of X, one in pink one in green", "make 3
variations of..."). Never split a single request describing ONE {medium} into several just because
it mentions multiple details together (colors, props, angles) — that's still ONE {medium} unless
the message explicitly asks for that many separate outputs.

If count > 1 (maximum {max_count} — if the request genuinely asks for more than that, cap it at
{max_count} and only write prompts for the first {max_count}):
- Write one clear, standalone descriptive prompt PER {medium} — each must be a complete, sensible
  request on its own (never "the same but in green" — restate what it's actually of, e.g. "a pink
  Ferrari on a race track", not just "in pink").
- Decide "sequential": true if a LATER {medium} genuinely depends on an EARLIER one's actual
  result existing first (e.g. "generate a car, then a video showing it driving off, based on that
  exact image" — the second cannot be made without the first's real output). false if the
  {medium}s are genuinely independent variants that could be produced in any order or at the same
  time (e.g. "one in pink, one in green" — neither needs the other to exist first). Default to
  false (independent) unless there's a real, stated dependency — most multi-{medium} requests are
  independent variants, not a sequence.

Return ONLY JSON:
{{
  "count": 1 or more,
  "prompts": ["..."],
  "sequential": true or false
}}
"""


@dataclass
class MultiGenerationPlan:
    prompts: list[str]
    sequential: bool


async def _plan_multi_generation(
    user_message: str, brief_idea: str, *, medium: str, max_count: int
) -> MultiGenerationPlan:
    """A real Tier-1 classification call, not a regex — splitting "2 images, one pink one green"
    into genuinely distinct, standalone prompts needs actual language understanding, the same
    reasoning this codebase already applies to routing (orchestrator.py) rather than keyword
    matching. Fails safe to a single-item plan (today's original, single-generation behavior) on
    any real failure — a broken planner must never block a normal turn."""
    single = MultiGenerationPlan(prompts=[user_message], sequential=False)
    if not user_message.strip():
        return single

    llm = get_llm_provider()
    context = f"User's request:\n{user_message}"
    if brief_idea:
        context += (
            f"\n\nBroader campaign context so far (supporting detail only — judge the count/split "
            f"from the request above, not this): {brief_idea}"
        )
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_MULTI_GENERATION_PLAN_PROMPT.format(medium=medium, max_count=max_count),
            messages=[{"role": "user", "content": context}],
            max_tokens=1024,
        )
        parsed = extract_json(result.text)
    except Exception as exc:
        log.warning("multi_generation_plan_failed", extra={"_extra_medium": medium, "_extra_error": str(exc)})
        return single

    count = int(parsed.get("count") or 1)
    prompts = [str(p).strip() for p in (parsed.get("prompts") or []) if str(p).strip()]
    if count <= 1 or len(prompts) <= 1:
        return single
    if len(prompts) > max_count:
        log.warning(
            "multi_generation_plan_truncated",
            extra={"_extra_medium": medium, "_extra_requested": len(prompts), "_extra_cap": max_count},
        )
        prompts = prompts[:max_count]
    return MultiGenerationPlan(prompts=prompts, sequential=bool(parsed.get("sequential", False)))


async def _run_multi_generation(
    plan: MultiGenerationPlan,
    *,
    run_one: Callable[[str, str | None], Awaitable[LeadResult]],
) -> list[LeadResult]:
    """Executes a plan's variants — genuinely SEQUENTIAL when the plan says so (a real correctness
    requirement: a dependent variant needs the prior one's real result, so it structurally cannot
    run any other way) or when `settings.multi_generation_parallel_enabled` is off (a real,
    deliberate cost/predictability override — independent variants CAN run concurrently, doesn't
    mean they must). Otherwise genuinely concurrent via `asyncio.gather`. Each variant's own
    failure degrades gracefully (logged, skipped) rather than losing every other already-succeeded
    (sometimes already-PAID-for) variant to one bad one — the same pattern `run_concurrent_specialists`
    already uses. `run_one(prompt, prior_variant_description)` — the second arg is only ever
    non-None in the sequential path, letting a later variant reference what the earlier one
    actually produced."""

    async def _safe(prompt: str, prior_desc: str | None) -> LeadResult | None:
        try:
            return await run_one(prompt, prior_desc)
        except SpecialistFailed as exc:
            log.warning("multi_generation_variant_failed", extra={"_extra_error": exc.message})
            return None

    if plan.sequential or not settings.multi_generation_parallel_enabled:
        results: list[LeadResult] = []
        prior_desc: str | None = None
        for prompt in plan.prompts:
            r = await _safe(prompt, prior_desc)
            if r is not None:
                results.append(r)
                prior_desc = (
                    r.metadata.get("image_prompt") or r.metadata.get("motion_prompt") or prompt
                )
        return results

    raw = await asyncio.gather(*[_safe(p, None) for p in plan.prompts])
    return [r for r in raw if r is not None]


def _variant_brief(brief: dict) -> dict:
    """A real, live-found bug (2026-09-22, caught during live verification): each multi-generation
    variant's own sub-prompt is already a complete, standalone request (the planning prompt
    explicitly requires this) — but every variant call still received the FULL original `brief`,
    including `idea`, which for a multi-subject request (e.g. "one video of a car, one of a
    mountain bike") still describes BOTH subjects together. `visual_design_lead.py`/
    `narrative_lead.py`'s own "current message first, brief.idea as secondary support" fix treats
    that idea as real supporting context, not noise — so a variant meant to be ONLY about the bike
    still generated shots mixing in the car from the other variant's subject. Stripping `idea` (and
    the equally combined `_last_option_labels`) from the brief passed to each variant's own
    generation call removes that bleed-through; every other real field (approval_mode, brand/
    product grounding scratch fields) stays intact and still applies to every variant equally."""
    return {k: v for k, v in brief.items() if k not in ("idea", "_last_option_labels")}


def _combine_multi_generation_results(results: list[LeadResult], *, requested_count: int | None = None) -> LeadResult:
    """The first variant becomes the turn's MAIN result (the element `session_service.py` creates
    directly); every other variant becomes an `extra_elements` entry — reusing the exact same
    mechanism already built for a Lead's real intermediate byproducts (scene stills, raw clips,
    voiceover tracks), rather than inventing a second "multiple results" shape. A variant's OWN
    `extra_elements` (e.g. each video variant's own scene-still/raw-clip/voiceover) are preserved
    too, not dropped.

    `requested_count` (2026-09-22, a real bug caught via live testing, not just review): a real
    request for "2 images" once produced only 1, with zero indication anything had gone wrong —
    `_run_multi_generation`'s own per-variant failure handling (correctly) keeps the turn from
    failing outright when one variant errors, but that silently degraded "2 requested" into "1
    delivered" with the exact same "Generated — check the canvas" message a full success gets
    (Rules.md: no fabricated success). When fewer variants succeeded than were actually asked for,
    a real, honest note is attached here so the caller can surface it instead of staying silent."""
    main = results[0]
    extras = list(main.extra_elements)
    for i, r in enumerate(results[1:], start=2):
        extras.append({
            "storage_ref": r.storage_ref,
            "element_type": r.element_type,
            "produced_by_specialist": r.produced_by_specialist,
            "metadata": {**r.metadata, "label": f"variant_{i}_of_{len(results)}"},
        })
        extras.extend(r.extra_elements)
    metadata = {**main.metadata, "multi_generation_count": len(results)}
    if requested_count is not None and len(results) < requested_count:
        metadata["partial_generation_note"] = (
            f"Only {len(results)} of the {requested_count} requested variants could be generated "
            f"— the rest failed and were skipped rather than failing the whole request."
        )
    return LeadResult(
        storage_ref=main.storage_ref,
        produced_by_specialist=main.produced_by_specialist,
        element_type=main.element_type,
        extra_elements=extras,
        metadata=metadata,
    )


@traceable(name="visual_design_lead_node")
async def _visual_design_lead_node(state: GraphState) -> GraphState:
    emit("lead_started", lead="visual_design_lead")
    brief = state.get("brief") or {}
    user_message = state.get("user_message") or ""
    try:
        plan = await _plan_multi_generation(
            user_message, brief.get("idea") or "", medium="image", max_count=_MAX_MULTI_IMAGE_COUNT
        )
        if len(plan.prompts) <= 1:
            result = await run_visual_design_lead(brief=brief, user_message=user_message)
        else:
            emit("multi_generation_plan", medium="image", count=len(plan.prompts), sequential=plan.sequential)

            async def _run_one(prompt: str, prior_desc: str | None) -> LeadResult:
                msg = (
                    prompt if not prior_desc
                    else f"{prompt}\n\n(For visual consistency, the previous image in this sequence depicted: {prior_desc})"
                )
                return await run_visual_design_lead(brief=_variant_brief(brief), user_message=msg)

            results = await _run_multi_generation(plan, run_one=_run_one)
            if not results:
                raise SpecialistFailed("visual_design_lead", "none of the requested image variants could be produced")
            result = _combine_multi_generation_results(results, requested_count=len(plan.prompts))
        # LeadResult -> dict only here, at the LangGraph-mandated TypedDict boundary (GraphState) —
        # the one accepted exception to "no dict crossing a layer boundary" (Rules.md section 2).
        state["result"] = result.to_dict()
        # A real fix for the "stale idea leaking forever" bug (2026-09-22, confirmed live via a
        # real user session whose `brief.idea` stayed "a modern minimalist logo" for hours,
        # bleeding into every later, completely unrelated request as "broader campaign context").
        # `ideation_service.py` deliberately freezes `brief.idea` the instant a session's first
        # element exists, to protect against a DIFFERENT bug (numeric erosion across ideation's own
        # option-picking rounds) — correct for THAT case, but it also meant `brief.idea` could never
        # again reflect reality once a session moved on to a genuinely new subject. This node only
        # runs for a real, FRESH `full_image` generation (never `direct_fix`), so a successful run
        # here really is the user's new current subject — updating `brief.idea` to it means the
        # NEXT turn's "earlier campaign notes" (leads/base.py's `stale_campaign_context_block`)
        # reflects what was actually just made, not something from hours or days earlier.
        if _is_substantive_request(user_message):
            brief["idea"] = user_message.strip()
            state["brief"] = brief
        emit("lead_completed", lead="visual_design_lead")
    except SpecialistFailed as exc:
        storage_ref = getattr(exc, "partial_storage_ref", None)
        log.error("visual_design_lead_failed", extra={"_extra_error": exc.message, "_extra_storage_ref": storage_ref})
        state["result"] = {
            "message": f"Ran into an issue with visual_design_lead: {exc.message}. How should we proceed?",
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        if storage_ref:
            state["result"]["storage_ref"] = storage_ref
            state["result"]["element_type"] = "image"
            state["result"]["produced_by_specialist"] = "illustrator"
        emit("lead_failed", lead="visual_design_lead", reason=exc.message)
    return state


_DURATION_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s\b|minutes?|mins?|m\b)", re.IGNORECASE)


def _extract_requested_duration_seconds(message: str) -> float | None:
    """A real, honest best-effort parse of an explicit clip length the user asked for (e.g. "a 10
    second audio", "30 seconds") — Kokoro (`providers/audio/local_kokoro.py`) has no duration
    parameter at all, so the only real lever Sound Designer has is how long a script it writes;
    this hands the target down as guidance instead of silently dropping it (2026-09-22, a real
    live-found bug: a user asked for 10 seconds and got ~4, because nothing upstream of Sound
    Designer's own prompt ever looked at the number in their message at all — it wrote whatever
    length line it felt like, unguided)."""
    match = _DURATION_PATTERN.search(message)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    return value * 60 if unit.startswith("m") else value


@traceable(name="full_audio_node")
async def _full_audio_node(state: GraphState) -> GraphState:
    """A brand-new standalone audio clip (2026-09-22, orchestrator.py's 4th route) — Sound
    Designer alone, no Lead sequence, since there's only one specialist that can do this at all.
    Deliberately NOT `_direct_fix_node` even though the mechanics look similar: that node's whole
    framing is "act on THIS existing asset" (it always includes `latest_element_*` context when
    available), which doesn't fit a genuinely fresh generation with no element assumed. Sound
    Designer's own prompt (sound_designer.md) never actually depended on an existing element's
    storage_ref — only campaign/shot context — so this reuses it directly, unconditionally without
    that framing. Only ever produces a spoken voiceover (`text_to_speech`) — Sound Designer has no
    real music-generation tool in this build; an honest limit stated in its own prompt, not hidden
    here either."""
    brief = state.get("brief") or {}
    user_message = state.get("user_message") or ""
    context_parts = [f"User request:\n{user_message}"]
    context_parts.append(f"{stale_campaign_context_block(brief)}{referenced_element_block(brief)}")
    requested_duration = _extract_requested_duration_seconds(user_message)
    if requested_duration:
        context_parts.append(
            f"Target spoken duration: approximately {requested_duration:.0f} seconds. There is NO "
            f"direct duration control on the TTS engine — hitting this depends entirely on writing "
            f"a script of roughly the right length (see your own instructions for the real "
            f"words-per-second guidance)."
        )
    context = "\n\n".join(context_parts)

    emit("lead_started", lead="full_audio")
    try:
        step = await run_specialist_with_review(
            "sound_designer",
            context=context,
            needs_retry=lambda r: not (
                r.latest_call("text_to_speech") and r.latest_call("text_to_speech").ok
            ),
            reminder=(
                "REMINDER: the user explicitly asked for a real audio clip. You MUST call "
                "text_to_speech with a real spoken line now — recommending 'music_only' or "
                "'silent' is not a valid response to an explicit request for audio."
            ),
        )
    except SpecialistFailed as exc:
        log.error("full_audio_failed", extra={"_extra_error": exc.message})
        state["result"] = {
            "message": f"Ran into an issue with full_audio: {exc.message}. How should we proceed?",
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        emit("lead_failed", lead="full_audio", reason=exc.message)
        return state

    call = step.latest_call("text_to_speech")
    if not call or not call.ok or not call.data.get("storage_ref"):
        # A real, honest non-result (Rules.md section 2) — never fabricated as if audio was
        # produced. The most likely real cause: the request wanted music, which this specialist
        # genuinely cannot make.
        state["result"] = {
            "message": "Sound Designer considered the request but did not produce an audio clip "
            "(only a spoken voiceover is supported here, never music).",
            "specialist_notes": step.data,
        }
        emit("lead_completed", lead="full_audio", no_op=True)
        return state

    state["result"] = {
        "storage_ref": call.data["storage_ref"],
        "produced_by_specialist": "sound_designer",
        "element_type": "audio",
        "metadata": {"voiceover_line": step.get("voiceover_line", ""), "notes": step.get("notes", "")},
    }
    # Same real "stop the stale idea leaking forever" fix as `_visual_design_lead_node`'s own —
    # this route always means a genuinely fresh, standalone audio request. `_is_substantive_request`
    # guards against a misrouted bare reply word corrupting it (see that function's own docstring).
    if _is_substantive_request(user_message):
        brief["idea"] = user_message.strip()
        state["brief"] = brief
    emit("lead_completed", lead="full_audio")
    return state


def _pending_approval_result(*, stage: str, message: str, proposal: dict) -> dict:
    """The same message+options+free-text shape ideation already uses — a real approval prompt
    reuses that existing, already-tested pattern rather than inventing a new response shape."""
    return {
        "message": message,
        "options": [
            {"id": "approve", "label": "Approve", "description": "Proceed to the next stage"},
            {"id": "revise", "label": "Request changes", "description": "Describe what to change"},
            # 2026-09-22, a real live-found gap (core/approval.py's own docstring) — the one real
            # escape hatch this gate was missing.
            {"id": "cancel", "label": "Cancel", "description": "Discard this proposal and start over"},
        ],
        "allow_free_text": True,
        "proposal": proposal,
    }


def _pending_motion_approval_result(*, message: str, proposal: dict) -> dict:
    """The Motion Lead gate is a real spend decision, not a text revision — there is no draft to
    iterate on, only whether to pay for the render. Distinct options from `_pending_approval_result`
    so the UI never implies a "request changes" loop that doesn't exist at this stage."""
    return {
        "message": message,
        "options": [
            {"id": "approve", "label": "Approve", "description": "Spend on the real video render"},
            {"id": "cancel", "label": "Cancel", "description": "Do not generate the video — nothing is spent"},
        ],
        "allow_free_text": True,
        "proposal": proposal,
    }


async def _run_batch(items: list[Any], *, sequential: bool, run_one: Callable[[Any], Awaitable[Any]]) -> list[Any]:
    """Generic sequential-vs-parallel executor for a batch of otherwise-independent inputs — no
    per-item failure swallowing (unlike `_run_multi_generation` below): a real failure here
    propagates and fails the whole batch, which is the right call for the narrative/scene PLANNING
    stages specifically — the user is about to review/approve a set of proposals, and silently
    dropping one without saying so would mean approving something incomplete without knowing it.
    `sequential=True` (a real dependency between variants) or `multi_generation_parallel_enabled`
    being off both force one-at-a-time execution — the same correctness/cost-control rule
    `_run_multi_generation` already applies, just generic over any return type (not just
    `LeadResult`) so both the planning stages here and the image path can share one real rule."""
    if sequential or not settings.multi_generation_parallel_enabled:
        return [await run_one(item) for item in items]
    return list(await asyncio.gather(*[run_one(item) for item in items]))


@traceable(name="full_video_pipeline_node")
async def _motion_lead_node(state: GraphState) -> GraphState:
    """
    The real full-video pipeline entry point — dispatches to the single-video flow (unchanged,
    `_run_single_video_node`) or the multi-video flow (`_run_multi_video_node`, 2026-09-22),
    depending on whether this request is asking for more than one DISTINCT video. Detected once,
    at the very start of a fresh request (`stage is None`), and persisted into
    `brief["multi_video_plan"]` so every later turn of a possibly-multi-turn approval flow keeps
    using the same plan rather than re-classifying an unrelated later message (an approval reply
    like "yes" is not itself a new video request to classify)."""
    brief = state.get("brief") or {}
    approval_mode = brief.get("approval_mode", "auto")
    stage = brief.get("video_stage")  # None | "narrative_pending" | "scene_pending" | "motion_pending" | "done"
    user_message = state.get("user_message") or ""

    if stage is None and "multi_video_plan" not in brief:
        plan = await _plan_multi_generation(
            user_message, brief.get("idea") or "", medium="video", max_count=_MAX_MULTI_VIDEO_COUNT
        )
        if len(plan.prompts) > 1:
            emit("multi_generation_plan", medium="video", count=len(plan.prompts), sequential=plan.sequential)
            brief["multi_video_plan"] = {"prompts": plan.prompts, "sequential": plan.sequential}

    multi_plan = brief.get("multi_video_plan")
    if multi_plan:
        return await _run_multi_video_node(state, brief, approval_mode, stage, user_message, multi_plan)
    return await _run_single_video_node(state, brief, approval_mode, stage, user_message)


async def _run_multi_video_node(
    state: GraphState,
    brief: dict,
    approval_mode: str,
    stage: str | None,
    user_message: str,
    multi_plan: dict,
) -> GraphState:
    """The multi-video counterpart to `_run_single_video_node` below — the SAME 3 real gates
    (narrative/scene/motion-spend) in "approve" mode, but each gate now carries a LIST of N
    proposals and is approved/revised as ONE BATCH (2026-09-22, per the user's explicit ask that
    parallel execution work in both auto AND approve/manual mode). A real, disclosed
    simplification: revision feedback applies to every video in the batch, not to one specific
    video by name — per-item revision targeting is a separate, larger feature this doesn't take on.

    Parallel vs sequential, in BOTH modes: genuinely INDEPENDENT videos (`multi_plan['sequential']`
    is False) run their narrative/scene/motion calls CONCURRENTLY across variants, at every stage —
    real parallel execution, not just "parallel after all approvals happen to be done". A
    genuinely DEPENDENT sequence always runs one variant's stage fully before the next starts
    (`_run_batch`'s own rule). `settings.multi_generation_parallel_enabled` can force everything
    sequential regardless — real cost/predictability control, since the motion stage's parallel
    branches are real, simultaneous paid Replicate renders."""
    prompts: list[str] = multi_plan["prompts"]
    sequential: bool = multi_plan["sequential"]
    n = len(prompts)

    def _stage_narratives_for_approval(narratives: list[NarrativePlan], *, revised: bool) -> GraphState:
        brief["video_stage"] = "narrative_pending"
        brief["narrative_plans"] = [x.to_dict() for x in narratives]
        state["brief"] = brief
        prefix = "Revised shots" if revised else "Proposed shots"
        lines = [f"Video {i + 1}: {'; '.join(x.shots)}. Story: {x.overall_story}" for i, x in enumerate(narratives)]
        state["result"] = _pending_approval_result(
            stage="narrative",
            message=f"{prefix} for {n} videos:\n" + "\n".join(lines),
            proposal={"narratives": [x.to_dict() for x in narratives]},
        )
        return state

    def _stage_scenes_for_approval(scenes: list[ScenePlan], *, revised: bool) -> GraphState:
        brief["video_stage"] = "scene_pending"
        brief["scene_plans"] = [x.to_dict() for x in scenes]
        state["brief"] = brief
        prefix = "Revised scenes" if revised else "Proposed scenes"
        lines = [
            f"Video {i + 1}: {x.environment_description} Lighting: {x.lighting_description}"
            for i, x in enumerate(scenes)
        ]
        state["result"] = _pending_approval_result(
            stage="scene",
            message=f"{prefix} for {n} videos:\n" + "\n".join(lines),
            proposal={"scenes": [x.to_dict() for x in scenes]},
        )
        return state

    def _stage_motion_for_approval(narratives: list[NarrativePlan], scenes: list[ScenePlan]) -> GraphState:
        brief["video_stage"] = "motion_pending"
        brief["narrative_plans"] = [x.to_dict() for x in narratives]
        brief["scene_plans"] = [x.to_dict() for x in scenes]
        state["brief"] = brief
        state["result"] = _pending_motion_approval_result(
            message=(
                f"All {n} shots and scenes are approved. Ready to render {n} real videos — this "
                f"step calls a paid provider (Replicate) {n} time{'s' if n != 1 else ''} — approve "
                f"to spend on all {n} renders, or send anything else to cancel with nothing spent."
            ),
            proposal={
                "narratives": [x.to_dict() for x in narratives],
                "scenes": [x.to_dict() for x in scenes],
            },
        )
        return state

    def _clear_multi_video_state() -> None:
        brief["video_stage"] = None
        brief.pop("multi_video_plan", None)
        brief.pop("narrative_plans", None)
        brief.pop("scene_plans", None)

    try:
        # === Stage 1: N NarrativePlans ===
        if stage == "narrative_pending":
            if is_approval(user_message):
                narratives = [NarrativePlan.from_dict(d) for d in brief["narrative_plans"]]
            elif is_cancel(user_message):
                _clear_multi_video_state()
                return _cancel_pending_video(state, brief, lead="narrative_lead", message=(
                    "Cancelled — the proposed shots were discarded and nothing was spent. Send a "
                    "new idea whenever you're ready."
                ))
            else:
                emit("lead_started", lead="narrative_lead", revision=True)
                revised_prompts = [f"{p}\n\nRevision feedback on the proposed shots: {user_message}" for p in prompts]
                narratives = await _run_batch(
                    revised_prompts, sequential=sequential,
                    run_one=lambda p: run_narrative_lead(brief=_variant_brief(brief), user_message=p),
                )
                emit("lead_completed", lead="narrative_lead", revision=True)
                return _stage_narratives_for_approval(narratives, revised=True)
        elif stage in ("scene_pending", "motion_pending"):
            narratives = [NarrativePlan.from_dict(d) for d in brief["narrative_plans"]]
        else:
            emit("lead_started", lead="narrative_lead")
            narratives = await _run_batch(
                prompts, sequential=sequential,
                run_one=lambda p: run_narrative_lead(brief=_variant_brief(brief), user_message=p),
            )
            emit("lead_completed", lead="narrative_lead")
            # Same real "stop the stale idea leaking forever" fix as `_visual_design_lead_node`'s
            # own — a genuinely fresh multi-video request, the real moment to refresh `brief.idea`.
            if _is_substantive_request(user_message):
                brief["idea"] = user_message.strip()
                state["brief"] = brief
            if approval_mode == "approve":
                return _stage_narratives_for_approval(narratives, revised=False)

        # === Stage 2: N ScenePlans ===
        if stage == "scene_pending":
            if is_approval(user_message):
                scenes = [ScenePlan.from_dict(d) for d in brief["scene_plans"]]
            elif is_cancel(user_message):
                _clear_multi_video_state()
                return _cancel_pending_video(state, brief, lead="scene_lead", message=(
                    "Cancelled — the proposed scenes were discarded and nothing was spent. Send a "
                    "new idea whenever you're ready."
                ))
            else:
                emit("lead_started", lead="scene_lead", revision=True)
                scenes = await _run_batch(
                    list(range(n)), sequential=sequential,
                    run_one=lambda i: run_scene_lead(
                        shot_description=f"{narratives[i].shots[0]}\n\nRevision feedback on the proposed scene: {user_message}",
                        brief=brief
                    ),
                )
                emit("lead_completed", lead="scene_lead", revision=True)
                return _stage_scenes_for_approval(scenes, revised=True)
        elif stage == "motion_pending":
            scenes = [ScenePlan.from_dict(d) for d in brief["scene_plans"]]
        else:
            emit("lead_started", lead="scene_lead")
            scenes = await _run_batch(
                list(range(n)), sequential=sequential, run_one=lambda i: run_scene_lead(shot_description=narratives[i].shots[0], brief=brief)
            )
            emit("lead_completed", lead="scene_lead")
            if approval_mode == "approve":
                return _stage_scenes_for_approval(scenes, revised=False)

        # === Stage 3: N paid Motion Lead renders — same "spend confirmation, not a text revision"
        # rule as the single-video flow, just covering the whole batch at once. ===
        if stage == "motion_pending":
            if not is_approval(user_message):
                _clear_multi_video_state()
                return _cancel_pending_video(state, brief, message=(
                    "Video generation cancelled — nothing was rendered and nothing was spent. Send "
                    "a new idea whenever you're ready."
                ))
        elif approval_mode == "approve":
            return _stage_motion_for_approval(narratives, scenes)

        emit("lead_started", lead="motion_lead")

        async def _render_one(i: int) -> LeadResult:
            return await run_motion_lead(brief=brief, narrative=narratives[i], scene=scenes[i])

        results: list[LeadResult] = []
        indices = list(range(n))
        if sequential or not settings.multi_generation_parallel_enabled:
            for i in indices:
                try:
                    results.append(await _render_one(i))
                except SpecialistFailed as exc:
                    log.warning("multi_video_variant_failed", extra={"_extra_error": exc.message})
        else:
            # A real, already-PAID-for render succeeding must never be lost because a SIBLING
            # render failed — `return_exceptions=True` (same rule Motion Lead's own internal
            # concurrency already uses for Sound Designer/Overlay Artist vs. the video path).
            raw = await asyncio.gather(*[_render_one(i) for i in indices], return_exceptions=True)
            for r in raw:
                if isinstance(r, SpecialistFailed):
                    log.warning("multi_video_variant_failed", extra={"_extra_error": r.message})
                elif isinstance(r, BaseException):
                    raise r
                else:
                    results.append(r)
        emit("lead_completed", lead="motion_lead")

        if not results:
            raise SpecialistFailed("motion_lead", "none of the requested video variants could be produced")

        _clear_multi_video_state()
        state["brief"] = brief
        # LeadResult -> dict only here, at the LangGraph-mandated TypedDict boundary — see the
        # matching comment in `_visual_design_lead_node` above.
        state["result"] = _combine_multi_generation_results(results, requested_count=n).to_dict()
    except SpecialistFailed as exc:
        log.error("full_video_pipeline_failed", extra={"_extra_error": exc.message})
        state["result"] = {
            "message": f"Ran into an issue with full_video_pipeline: {exc.message}. How should we proceed?",
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        emit("lead_failed", lead="full_video_pipeline", reason=exc.message)
    return state


def _is_substantive_request(text: str) -> bool:
    """A real, live-found bug (2026-09-22): the "refresh `brief.idea` instead of leaving it frozen
    forever" fix (see `_visual_design_lead_node`'s own comment) trusted that REACHING a fresh
    -generation node meant the current message was a real, new subject worth remembering — true
    most of the time, but a real user session's `brief.idea` was found corrupted to the literal
    string `"approve"` after the orchestrator's own well-documented classification flakiness
    (Memory.md, 2026-09-21) misrouted a bare approval/reply word into a fresh-generation route
    instead of wherever it actually belonged. A short reply word is never a real campaign subject —
    this is a real, deterministic backstop (same reasoning as `_is_bare_greeting`/
    `_check_price_stated`: a plain check beats trusting an LLM's routing to always be right) so
    `brief.idea` can only ever be overwritten by something that actually looks like real content,
    never a stray "approve"/"yes"/"ok" that slipped through a misroute."""
    stripped = text.strip()
    if not stripped or is_approval(stripped) or is_cancel(stripped):
        return False
    return len(stripped) >= 12 or len(stripped.split()) >= 3


def _cancel_pending_video(state: GraphState, brief: dict, *, message: str, lead: str = "motion_lead") -> GraphState:
    """Shared reset for every "abandon this staged video pipeline" exit — the narrative/scene
    cancel option (2026-09-22) and the pre-existing motion-spend cancel both need the exact same
    real cleanup: clear the stage marker and whatever plan(s) were staged, so the NEXT message
    starts completely fresh rather than resuming into a dead stage."""
    brief["video_stage"] = None
    brief.pop("narrative_plan", None)
    brief.pop("scene_plan", None)
    state["brief"] = brief
    state["result"] = {"message": message}
    emit("lead_failed", lead=lead, reason="cancelled_before_spend")
    return state


async def _run_single_video_node(
    state: GraphState, brief: dict, approval_mode: str, stage: str | None, user_message: str
) -> GraphState:
    """
    The real, ORIGINAL single-video pipeline: Narrative Lead -> Scene Lead -> Motion Lead
    (Architecture.md section 2.1) — completely unchanged by the 2026-09-22 multi-video work above
    (that work dispatches here whenever a request isn't asking for more than one distinct video, so
    this function's own real, already-tested behavior stays exactly as it was).

    In "auto" mode (default): runs straight through with no pauses, exactly as it always has.

    In "approve" mode (Memory.md, Phase 4 — a real user ask for genuine approval checkpoints, not
    just after-the-fact fixes): pauses after Narrative Lead, after Scene Lead, AND before Motion
    Lead itself — a real, explicit spend confirmation before the one pay-per-use step in this
    whole pipeline (Replicate), closing a real gap where the previous two gates covered the
    creative plan but not the actual money being spent to render it. The plan is staged in the
    session's own `brief` (`video_stage`, `narrative_plan`, `scene_plan`) — the same "resend
    accumulated state each turn" pattern this whole graph already uses for multi-turn ideation,
    just extended to gate between Leads too, rather than adopting LangGraph's own
    interrupt/checkpointer machinery.
    """

    def _stage_narrative_for_approval(narrative: NarrativePlan, *, revised: bool) -> GraphState:
        brief["video_stage"] = "narrative_pending"
        brief["narrative_plan"] = narrative.to_dict()
        state["brief"] = brief
        prefix = "Revised shots" if revised else "Proposed shots"
        state["result"] = _pending_approval_result(
            stage="narrative",
            message=f"{prefix}: {'; '.join(narrative.shots)}. Story: {narrative.overall_story}",
            proposal=narrative.to_dict(),
        )
        return state

    def _stage_scene_for_approval(scene: ScenePlan, *, revised: bool) -> GraphState:
        brief["video_stage"] = "scene_pending"
        brief["scene_plan"] = scene.to_dict()
        state["brief"] = brief
        prefix = "Revised scene" if revised else "Proposed scene"
        state["result"] = _pending_approval_result(
            stage="scene",
            message=f"{prefix}: {scene.environment_description} Lighting: {scene.lighting_description}",
            proposal=scene.to_dict(),
        )
        return state

    def _stage_motion_for_approval(narrative: NarrativePlan, scene: ScenePlan) -> GraphState:
        brief["video_stage"] = "motion_pending"
        brief["narrative_plan"] = narrative.to_dict()
        brief["scene_plan"] = scene.to_dict()
        state["brief"] = brief
        state["result"] = _pending_motion_approval_result(
            message=(
                f"Both the shots and the scene are approved. Ready to render the real video for: "
                f"{narrative.shots[0]} This step calls a paid provider (Replicate) — approve to "
                f"spend on the render, or send anything else to cancel with nothing spent."
            ),
            proposal={"narrative": narrative.to_dict(), "scene": scene.to_dict()},
        )
        return state

    try:
        # === Stage 1: get a NarrativePlan, either fresh, approved-from-staged, or revised ===
        if stage == "narrative_pending":
            if is_approval(user_message):
                narrative = NarrativePlan.from_dict(brief["narrative_plan"])
            elif is_cancel(user_message):
                return _cancel_pending_video(state, brief, lead="narrative_lead", message=(
                    "Cancelled — the proposed shots were discarded and nothing was spent. Send a "
                    "new idea whenever you're ready."
                ))
            else:
                emit("lead_started", lead="narrative_lead", revision=True)
                # Real, live-found bug (2026-09-22): this used to build `idea` by blindly appending
                # the new message as "revision feedback" onto the OLD `brief.idea` — which silently
                # assumed every non-approval reply is incremental feedback on the SAME shots. A
                # message that's actually a genuinely different request (e.g. referencing a
                # different existing element than whatever grounded the original proposal) got
                # buried as an afterthought behind stale context instead of driving the result, the
                # same class of bug already fixed once for `_check_followup_clarity`/
                # `visual_design_lead.py` — just not yet here, since this resume path never goes
                # through either of those. Now matches their same "current message first, old
                # context second, only as real supporting detail" shape, and explicitly includes
                # `latest_element_description` (session_service.py) — the fix that made audio
                # references actually work everywhere else, extended to apply here too.
                old_shots = NarrativePlan.from_dict(brief["narrative_plan"]).shots
                revision_message = user_message
                referenced_elements = brief.get("referenced_elements_context", [])
                if referenced_elements:
                    elements_desc = []
                    for i, el in enumerate(referenced_elements, 1):
                        kind = el.get("element_type", "unknown kind")
                        desc = el.get("description", "(no description recorded)")
                        elements_desc.append(f"Element {i} (Type: {kind}): {desc}")
                    elements_str = "\n".join(elements_desc)
                    revision_message += (
                        f"\n\n(This request references the following existing elements:\n{elements_str})"
                    )
                revision_message += (
                    f"\n\n(Previously proposed shots — revise THESE only if the message above is "
                    f"feedback on them; ignore them entirely if the message above is really a new "
                    f"or different request: {json.dumps(old_shots)})"
                )
                narrative = await run_narrative_lead(brief=brief, user_message=revision_message)
                emit("lead_completed", lead="narrative_lead", revision=True)
                return _stage_narrative_for_approval(narrative, revised=True)
        elif stage in ("scene_pending", "motion_pending"):
            # Narrative was already approved in an earlier turn — reload it, never regenerate it
            # (a real bug caught before this ever ran live: resuming at the scene stage must not
            # silently re-run Narrative Lead from scratch — the same reload applies at the motion
            # gate too, one turn further on).
            narrative = NarrativePlan.from_dict(brief["narrative_plan"])
        else:
            emit("lead_started", lead="narrative_lead")
            # `user_message` passed through as the real, live current-request driver — same fix
            # (2026-09-22) as `_visual_design_lead_node`'s own: a fresh video request must not be
            # generated against a stale, frozen `brief.idea` left over from an earlier, unrelated
            # part of this same session.
            narrative = await run_narrative_lead(brief=brief, user_message=user_message)
            emit("lead_completed", lead="narrative_lead")
            # Same real "stop the stale idea leaking forever" fix as `_visual_design_lead_node`'s
            # own (see that node's comment) — this branch only runs for a genuinely FRESH video
            # request (never a revision/resume), so it's the right moment to update `brief.idea` to
            # what was actually just asked for, rather than leaving it frozen at whatever it was
            # when this session's first element was ever created.
            if _is_substantive_request(user_message):
                brief["idea"] = user_message.strip()
                state["brief"] = brief
            if approval_mode == "approve":
                return _stage_narrative_for_approval(narrative, revised=False)

        # === Stage 2: get a ScenePlan, either fresh, approved-from-staged, or revised ===
        if stage == "scene_pending":
            if is_approval(user_message):
                scene = ScenePlan.from_dict(brief["scene_plan"])
            elif is_cancel(user_message):
                return _cancel_pending_video(state, brief, lead="scene_lead", message=(
                    "Cancelled — the proposed scene was discarded and nothing was spent. Send a "
                    "new idea whenever you're ready."
                ))
            else:
                emit("lead_started", lead="scene_lead", revision=True)
                scene = await run_scene_lead(
                    shot_description=f"{narrative.shots[0]}\n\nRevision feedback on the proposed scene: {user_message}",
                    brief=brief
                )
                emit("lead_completed", lead="scene_lead", revision=True)
                return _stage_scene_for_approval(scene, revised=True)
        elif stage == "motion_pending":
            # Scene was already approved in an earlier turn too — reload it, never regenerate it.
            scene = ScenePlan.from_dict(brief["scene_plan"])
        else:
            emit("lead_started", lead="scene_lead")
            scene = await run_scene_lead(shot_description=narrative.shots[0], brief=brief)
            emit("lead_completed", lead="scene_lead")
            if approval_mode == "approve":
                return _stage_scene_for_approval(scene, revised=False)

        # === Stage 3: Motion Lead — the only pay-per-use step. In "approve" mode this is a real
        # spend confirmation, separate from the two creative-plan gates above (Memory.md): the
        # plan being right and the money being worth spending on it are different judgments. ===
        if stage == "motion_pending":
            if not is_approval(user_message):
                # No text to revise here, only a spend decision — an unclear reply must never be
                # treated as "spend the money anyway" (core/approval.py's own rule), so anything
                # short of a clear approval cancels rather than looping or silently proceeding.
                return _cancel_pending_video(state, brief, message=(
                    "Video generation cancelled — nothing was rendered and nothing was spent. Send "
                    "a new idea whenever you're ready."
                ))
            # Approved — fall through to the real, paid render below.
        elif approval_mode == "approve":
            return _stage_motion_for_approval(narrative, scene)

        emit("lead_started", lead="motion_lead")
        result = await run_motion_lead(brief=brief, narrative=narrative, scene=scene)
        emit("lead_completed", lead="motion_lead")
        brief["video_stage"] = "done"
        state["brief"] = brief
        # LeadResult -> dict only here, at the LangGraph-mandated TypedDict boundary — see the
        # matching comment in _visual_design_lead_node above.
        state["result"] = result.to_dict()
    except SpecialistFailed as exc:
        log.error("full_video_pipeline_failed", extra={"_extra_error": exc.message})
        state["result"] = {
            "message": f"Ran into an issue with full_video_pipeline: {exc.message}. How should we proceed?",
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        emit("lead_failed", lead="full_video_pipeline", reason=exc.message)
    return state


_INTENT_FIELDS = (
    "overlay_text", "image_prompt", "motion_prompt", "aesthetic_direction",
    "scene_description", "notes", "message",
)


def _describe_specialist_intent(data: dict) -> str | None:
    """A short, honest summary of what a specialist WAS thinking, pulled from whichever of its own
    final-JSON fields actually has real content — used only to make a real dead-end clarifying
    question more specific (2026-09-22, `_direct_fix_node`'s own comment), never fabricated: if
    none of these fields has anything, returns None and the caller's question stays generic."""
    for field in _INTENT_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@traceable(name="direct_fix_node")
async def _direct_fix_node(state: GraphState) -> GraphState:
    """A real single-specialist call, bypassing its Lead entirely — Architecture.md section 1's
    third route ("just fix the overlay" -> Overlay Artist alone), not the pipeline's normal
    multi-specialist sequence. Whatever tool the specialist genuinely chooses to call and that
    produces a real storage_ref becomes this turn's result — generic across every specialist,
    since which tool applies depends entirely on which specialist was targeted."""
    brief = state.get("brief") or {}
    target = state.get("target_specialist")
    if not target:
        state["result"] = {"message": "Could not determine which specialist should handle this fix."}
        return state

    # Fix 8 (2026-09-26) was considered here too (this route — "just fix/edit this specific
    # element" — reasons from plain description strings, no image ever attached, the literal
    # "JSON alone" anti-pattern), but deliberately NOT applied yet: `target` can be any specialist
    # (composition_artist, overlay_artist, ...), each running on a Tier 1/2/3 model
    # (`core/config.py`'s own comment confirms these gpt-oss models are text-only — a dedicated
    # SEPARATE `groq_vision_model` exists specifically because they can't see images). Attaching
    # real image content here would either error and silently escalate every such turn to the
    # paid Replicate fallback (a real cost/latency regression, not a free win) or do nothing useful
    # — genuinely giving this real vision grounding needs routing through `vision.py`'s
    # `complete_with_vision` (or confirming these tiers actually support it), a separate,
    # deliberate integration decision, not something to attach speculatively. Left as the
    # pre-existing plain-text context for now.
    import json

    context_parts = [f"User request:\n{state.get('user_message', '')}"]
    if brief.get("idea"):
        context_parts.append(f"Campaign idea so far:\n{brief['idea']}")
    referenced_elements = brief.get("referenced_elements_context", [])
    if referenced_elements:
        context_parts.append("The following existing generated elements are available to reference or fix:")
        for i, el in enumerate(referenced_elements, 1):
            ref = el.get("storage_ref")
            kind = el.get("element_type", "unknown")
            desc = el.get("description") or NO_DESCRIPTION_SENTINEL
            context_parts.append(
                f"Element {i} (storage_ref: {ref}, type: {kind}) depicts:\n{desc}"
            )
        context_parts.append(
            "Act on the relevant existing asset(s), keeping everything the same except what the user's "
            "request asks to change — do not generate an unrelated new one."
        )

    direct_fix_context = "\n\n".join(context_parts)
    direct_fix_context += f"\n\nFull session brief context:\n{json.dumps(brief)}"
    emit("lead_started", lead="direct_fix", target_specialist=target)
    try:
        # Real, live-found issue (2026-09-22, cross-check pass): a specialist can decline to act
        # at all even when the user gave an explicit, specific instruction — its own prompt's
        # legitimate "skip if nothing needs changing" escape hatch firing on a genuine direct
        # request instead. Routed through the same shared review/retry mechanism as Illustrator
        # and Sound Designer (Tasks.md #3): one bounded reconsideration, not a forced action — the
        # specialist may still legitimately decline a second time if a change truly isn't
        # warranted, but it gets one real chance to reconsider first, same as every other
        # "claimed vs. actually did" gap already found and fixed this session.
        step = await run_specialist_with_review(
            target,
            context=direct_fix_context,
            needs_retry=lambda r: _produced_ref(r)[0] is None,
            reminder=(
                "REMINDER: the user gave an explicit, specific instruction and your previous "
                "attempt made no tool call at all. If a real change is genuinely warranted, make "
                "it now — only skip again if you have a concrete reason no change applies."
            ),
        )
    except SpecialistFailed as exc:
        log.error("direct_fix_failed", extra={"_extra_specialist": target, "_extra_error": exc.message})
        state["result"] = {
            "message": f"Ran into an issue with {target}: {exc.message}. How should we proceed?",
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        emit("lead_failed", lead="direct_fix", reason=exc.message)
        return state

    produced_ref, produced_tool = _produced_ref(step)

    if not produced_ref:
        # The specialist ran and reasoned, but genuinely made no tool call that produced a new
        # asset — an honest non-result, not an error (Rules.md section 2: no fabricated success).
        # A real, live-found gap (2026-09-22, per an explicit user ask: "don't assume, make the
        # agent ask a question if there are doubts"): this used to be a flat, dead-end statement —
        # true, but not actually a QUESTION, and gave the user nothing to react to even though free
        # text was already silently accepted here. Deliberately does NOT offer a "try again" pickable
        # OPTION — that would resend just that option's own short label as the next `user_message`
        # (`session_service.py`'s `_last_option_labels` lookup), the exact same fragment-loss bug
        # just fixed for `ideation_service.py`'s own clarification loop, just reintroduced here if
        # copied naively. Free text stays open (already the default) so the user's own real reply
        # is what actually gets read next turn — never a resent label standing in for it.
        specialist_intent = _describe_specialist_intent(step.data)
        state["result"] = {
            "message": (
                f"{target} considered this{f' — it was thinking: {specialist_intent}' if specialist_intent else ''}, "
                f"but wasn't confident enough to actually apply a change. Could you say more "
                f"specifically what you'd like — the exact text/placement/detail — so it can act on "
                f"it directly?"
            ),
            "specialist_notes": step.data,
        }
        emit("lead_completed", lead="direct_fix", no_op=True)
        return state

    # Real, live-found bug (2026-09-22): If Sound Designer was called directly, it doesn't mux
    # audio by itself, only sets `should_mux`. The caller must run the muxing tool directly.
    if target == "sound_designer" and step.data.get("should_mux"):
        base_ref = brief.get("latest_element_storage_ref")
        if base_ref:
            from ...services.tools.registry import get_tool
            mux_result = await get_tool("mux_audio_into_video").run(
                {"video_storage_ref": base_ref, "audio_storage_ref": produced_ref}
            )
            if mux_result.ok and mux_result.data.get("storage_ref"):
                produced_ref = mux_result.data["storage_ref"]
                produced_tool = "mux_audio_into_video"
            else:
                log.warning("direct_fix_mux_failed", extra={"_extra_error": mux_result.error})

    extra_elements = []
    for c in step.tool_calls:
        if c.ok and c.data.get("storage_ref") and c.data["storage_ref"] != produced_ref:
            extra_elements.append({
                "storage_ref": c.data["storage_ref"],
                "element_type": _ELEMENT_TYPE_BY_TOOL.get(c.tool_name, "text"),
                "produced_by_specialist": target,
                "metadata": {"direct_fix": True, "tool_used": c.tool_name},
            })

    result: dict = {
        "storage_ref": produced_ref,
        "produced_by_specialist": target,
        # Real, live-found bug (2026-09-21): this used to inherit the PREVIOUS element's type from
        # the brief regardless of what the tool actually just produced — so a direct_fix that
        # genuinely generates a video (video_editor_cutter -> video_stitcher) onto an element that
        # started life as an image kept `element_type: "image"`, and the frontend tried to render
        # the real MP4 bytes inside an `<img>` tag, which silently fails — looking to the user like
        # the whole canvas tile just vanished, when the video itself was actually produced fine.
        # Derived from which tool genuinely produced this result instead; an unrecognized tool
        # (there shouldn't be one, but a future addition might be missed here) falls back to the
        # old brief-based guess rather than crashing the turn outright.
        "element_type": _ELEMENT_TYPE_BY_TOOL.get(
            produced_tool, brief.get("latest_element_type", "image")
        ),
        "metadata": {"direct_fix": True, "tool_used": produced_tool, **step.data},
        "extra_elements": extra_elements,
    }
    if brief.get("latest_element_id") and produced_tool not in _ANNOTATION_ONLY_TOOLS:
        result["update_existing_element_id"] = brief["latest_element_id"]
    state["result"] = result
    emit("lead_completed", lead="direct_fix")
    return state


@traceable(name="dynamic_executor_node")
async def _dynamic_executor_node(state: GraphState) -> GraphState:
    """Dynamically executes a plan of specialists generated by the Orchestrator.
    Handles sequential execution, state passing, and robust error recovery."""
    plan = state.get("dynamic_plan")
    if not plan:
        state["result"] = {"message": "Dynamic plan was empty."}
        return state

    brief = state.get("brief") or {}
    user_message = state.get("user_message") or ""
    import json
    
    current_context = [{"type": "text", "text": f"Campaign idea so far:\n{brief.get('idea') or user_message}"}]

    # Real, live-found ordering fix (2026-09-26, decomposition-quality investigation): the full
    # brief JSON dump used to come AFTER the referenced-element context and BEFORE the
    # aspect-ratio hint — burying the actual actionable instructions behind the biggest, noisiest
    # block right before the model has to act. Moved here instead, right after the campaign idea
    # and well before anything that needs to stay salient — the referenced-element context and the
    # aspect-ratio hint below are now the LAST things the model reads, not buried before a JSON
    # wall.
    current_context[0]["text"] += f"\n\nFull session brief context:\n{json.dumps(brief)}"

    referenced_elements = brief.get("referenced_elements_context", [])
    if referenced_elements:
        # Fix 8 (2026-09-26): real image + real JSON together, never JSON alone — shared with
        # `_direct_fix_node` and `orchestrator.py`'s classifier so all three places that reason
        # about "what is this element" see the same real image+metadata pairing.
        from ...core.element_context import build_element_context_blocks
        current_context.append({
            "type": "text",
            "text": "The following existing generated elements are available to reference or fix:",
        })
        for i, el in enumerate(referenced_elements, 1):
            blocks = build_element_context_blocks(el)
            blocks[0]["text"] = f"Element {i}: {blocks[0]['text']}\n(Note: If generating a new visual base from this, pass this storage_ref as 'reference_storage_ref' to base_image_generator)"
            current_context.append(blocks[0])
            current_context.extend(blocks[1:])

    # Deterministic aspect-ratio backstop (2026-09-25) — same shared helper `visual_design_lead.py`
    # uses for the `full_image` route; this is the OTHER call site (Task plan item 4), since a
    # dynamic-routed edit/follow-up request never goes through visual_design_lead.py at all. Its
    # own trailing block now (not appended into block 0) so it stays the LAST/most recent thing
    # before the model acts, regardless of how many referenced-element blocks came before it.
    from ..leads.base import aspect_ratio_hint_block
    hint = aspect_ratio_hint_block(user_message or brief.get("idea") or "")
    if hint:
        current_context.append({"type": "text", "text": hint})

    latest_storage_ref = None
    latest_tool = None
    last_completed_specialist = None
    all_metadata = {}
    extra_elements = []

    # Only enforce asset generation for specialists that actually produce assets,
    # not for planning/strategy specialists (like reference_curator or palette_strategist)
    generating_specialists = {"base_image_generator", "overlay_artist", "image_animator", "sound_designer", "upscaler", "outpainter"}

    async def _run_one_step(i: int, step_info: dict, latest_ref_snapshot: str | None):
        """Builds one step's context and runs it — a pure function of the plan/brief/context plus
        an explicit `latest_ref_snapshot` (never the outer loop's own mutable `latest_storage_ref`
        directly), so this is safe to call either sequentially (with the up-to-date value) or
        concurrently as part of a verified-safe parallel group (all members get the SAME
        pre-group snapshot — none of them can see a sibling's not-yet-produced output, by
        design)."""
        import copy
        specialist = step_info.get("specialist")
        instruction = step_info.get("instruction", "")

        step_context = copy.deepcopy(current_context)
        # Real, live-found gap (2026-09-26, decomposition-quality investigation): coordination
        # across a multi-step dynamic plan was purely sequential pass-forward — nothing
        # re-stated the ORIGINAL collective goal at each step, so a later step could drift from
        # it after several steps' worth of accumulated context. Cheaper than a new
        # cross-step verification LLM call (which this session's own cost-efficiency principle
        # argues against): just repeat a short, fixed anchor at every step instead.
        goal_anchor = f"Overall collective goal (do not drift from this): {brief.get('idea') or user_message}"
        instruction_text = f"\n\n{goal_anchor}\n\nYOUR SPECIFIC INSTRUCTION FOR THIS STEP:\n{instruction}"
        if latest_ref_snapshot:
            instruction_text += f"\n\nThe previous step generated/modified an asset. Its storage_ref is: {latest_ref_snapshot}. Use this asset as your source image/video if applicable."
        elif not referenced_elements:
            # Real, live-found bug (2026-09-25, live-reproduced: a fresh request with nothing
            # to reference yet — e.g. "make a mclaren campaign post" — reached `palette_strategist`
            # right after `reference_curator` produced no real asset). Nothing in this step's
            # context ever told the specialist a real image genuinely doesn't exist yet, so a
            # model asked to use `color_palette_extractor` (which requires a real `storage_ref`)
            # guessed/hallucinated one, got a real "asset not found" tool failure, then — with
            # no clear instruction for how to recover — answered in plain English instead of
            # its required JSON shape, failing the whole plan. This is a deterministic, structural
            # fact about the plan's current state (not something worth another LLM guess), stated
            # explicitly so any tool needing a real image is correctly skipped instead of guessed.
            instruction_text += (
                "\n\nNO REAL IMAGE OR ASSET EXISTS YET for this request — no reference was "
                "given and no earlier step in this plan has produced one. Do NOT call any tool "
                "that requires an existing storage_ref (e.g. color_palette_extractor, "
                "image_editor) — you have no real storage_ref to give it, and guessing one will "
                "fail. Work from the written campaign/brand/product context only, or if your "
                "role genuinely cannot proceed without a real image, respond with your required "
                "JSON shape and an \"error\" field explaining why — never plain prose."
            )

        # Find the text dict and append the instruction to it
        for part in step_context:
            if part.get("type") == "text":
                part["text"] += instruction_text
                break

        # Run the specialist with self-correction retry, passing brief so the real
        # _recent_chat_history is injected as proper conversation messages (enabling
        # user "go ahead" / override confirmations to actually reach the LLM).
        return await run_specialist_with_review(
            specialist,
            context=step_context,
            needs_retry=lambda r, specialist=specialist: (
                specialist in generating_specialists and _produced_ref(r)[0] is None
            ),
            reminder="REMINDER: You must call a tool to fulfill your instruction and produce an asset.",
            brief=brief,
        )

    def _group_plan_steps(plan_steps: list[dict]) -> list[list[int]]:
        """Groups plan step indices by the orchestrator's own `parallel_group` claim (missing/None
        => its own singleton group) — preserves overall plan order; a group's position is where
        its FIRST member appears."""
        groups: list[list[int]] = []
        group_pos: dict[object, int] = {}
        for idx, s in enumerate(plan_steps):
            gid = s.get("parallel_group")
            if gid is None:
                groups.append([idx])
                continue
            if gid in group_pos:
                groups[group_pos[gid]].append(idx)
            else:
                group_pos[gid] = len(groups)
                groups.append([idx])
        return groups

    emit("lead_started", lead="dynamic_executor")
    try:
        for group_indices in _group_plan_steps(plan):
            group_indices = [gi for gi in group_indices if plan[gi].get("specialist")]
            if not group_indices:
                continue

            # Code-level verification of the orchestrator's own `parallel_group` claim — never
            # blindly trusted. Only a group with 2+ real members, none of which mutate an existing
            # asset (`_ASSET_MUTATING_SPECIALISTS`), is dispatched concurrently; anything else runs
            # sequentially exactly as before (always correct, just not necessarily fastest).
            is_verified_parallel = len(group_indices) > 1 and not any(
                plan[gi].get("specialist") in _ASSET_MUTATING_SPECIALISTS for gi in group_indices
            )

            if is_verified_parallel:
                emit("dynamic_plan_group_parallel", step_count=len(group_indices))
                # All group members get the SAME pre-group snapshot — none can see a sibling's
                # not-yet-produced output, by design (that's exactly what makes this safe).
                branches = {
                    f"step_{gi}_{plan[gi].get('specialist')}": _run_one_step(gi, plan[gi], latest_storage_ref)
                    for gi in group_indices
                }
                results_by_key = await run_concurrent_specialists(branches)
                step_results = [results_by_key[f"step_{gi}_{plan[gi].get('specialist')}"] for gi in group_indices]
            else:
                # Sequential: each call's snapshot is the immediately preceding step's own output
                # within this same group — a purely local variable, not yet folded into the outer
                # `latest_storage_ref`/`extra_elements` bookkeeping (that happens uniformly below,
                # for both paths, so the fold logic is never duplicated/inconsistent between them).
                step_results = []
                running_ref = latest_storage_ref
                for gi in group_indices:
                    result = await _run_one_step(gi, plan[gi], running_ref)
                    step_results.append(result)
                    produced_ref, _ = _produced_ref(result)
                    if produced_ref:
                        running_ref = produced_ref

            # Fold every step's result in the group back into shared state, in stable plan order —
            # ONE unified fold, identical regardless of whether the group ran concurrently or
            # sequentially (this is exactly the original single-step logic, just applied per
            # group member instead of per individual step).
            for gi, step_result in zip(group_indices, step_results):
                specialist = plan[gi].get("specialist")
                produced_ref, produced_tool = _produced_ref(step_result)

                for c in step_result.tool_calls:
                    ref = c.data.get("storage_ref")
                    if c.ok and ref and ref != produced_ref:
                        extra_elements.append({
                            "storage_ref": ref,
                            "element_type": _ELEMENT_TYPE_BY_TOOL.get(c.tool_name, "text"),
                            "produced_by_specialist": specialist,
                            "metadata": {"dynamic_plan_step": True, "tool_used": c.tool_name},
                        })

                if produced_ref:
                    if latest_storage_ref and latest_storage_ref != produced_ref:
                        extra_elements.append({
                            "storage_ref": latest_storage_ref,
                            "element_type": _ELEMENT_TYPE_BY_TOOL.get(latest_tool, brief.get("latest_element_type", "image")),
                            "produced_by_specialist": last_completed_specialist,
                            "metadata": {"dynamic_plan_step": True, "tool_used": latest_tool},
                        })
                    latest_storage_ref = produced_ref
                    latest_tool = produced_tool
                    last_completed_specialist = specialist

                all_metadata[f"step_{gi}_{specialist}"] = step_result.data

    except SpecialistFailed as exc:
        # Real, live-found bug (2026-09-24, per an explicit user report: "most of the generations
        # are taking place perfectly but they are not being shown"): a LATER step in this plan
        # failing used to discard EVERYTHING — including a real asset an EARLIER step in the same
        # plan already produced (`latest_storage_ref`, tracked precisely for this reason but never
        # actually used on this path before). E.g. a plan of
        # [reference_curator, illustrator, composition_artist] where illustrator genuinely
        # generates a real image, then composition_artist fails on an unrelated guardrail conflict
        # — the real image existed, was never attached to any canvas element, and the user was
        # shown "ran into an issue, try again" with nothing to show for the real work (and any real
        # cost) already done. Mirrors the same real degrade `motion_lead.py` already uses when
        # `video_editor_cutter` fails after Camera Director's real paid render succeeds — a partial,
        # real result beats a discarded one every time.
        log.warning(
            "dynamic_plan_partial_failure" if latest_storage_ref else "dynamic_plan_failed",
            extra={"_extra_error": exc.message, "_extra_partial_ref": latest_storage_ref},
        )
        if not latest_storage_ref:
            state["result"] = {
                "message": f"Ran into an issue while executing the plan: {exc.message}. How should we proceed?",
                "options": [
                    {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                    {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
                ],
                "allow_free_text": True
            }
            emit("lead_failed", lead="dynamic_executor", reason=exc.message)
            return state
        # A real, partial result exists — fall through to the same success-shaped result the loop
        # would have built had it finished normally, using the LAST STEP THAT ACTUALLY COMPLETED
        # (not `plan[-1]`, which may never have run at all).
        all_metadata["partial_failure"] = exc.message
    except SpecialistNotFound as exc:
        # Real, live-found bug (2026-09-23): a raw HTTP 500 in production — a hallucinated
        # specialist name in the plan (e.g. "style_board_planner", never registered anywhere)
        # raised this UNCAUGHT here before, since only `SpecialistFailed` was handled. The real
        # prevention is upstream now (`orchestrator.py` validates every plan step's specialist name
        # against the real registry before this node ever runs), but this stays as a genuine
        # defense-in-depth backstop — the same "never a raw crash, always a real disclosed message"
        # principle every other Lead/direct_fix path in this file already follows.
        log.error("dynamic_plan_unknown_specialist", extra={"_extra_error": str(exc)})
        state["result"] = {
            "message": f"The plan named a specialist that doesn't exist ({exc}). How should we proceed?",
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        emit("lead_failed", lead="dynamic_executor", reason=str(exc))
        return state

    if not latest_storage_ref:
        # Surface the most meaningful specialist note — prefer an "error" field (MASTER_DIRECTIVE
        # graceful-fail response) over a generic message so the user sees WHY nothing was generated
        # (e.g. "which phone model?" rather than the opaque "did not produce any visible assets").
        specialist_error = None
        for meta in all_metadata.values():
            if isinstance(meta, dict) and meta.get("error"):
                specialist_error = meta["error"]
                break
        state["result"] = {
            "message": specialist_error or "The plan executed but did not produce any visible assets.",
            "specialist_notes": all_metadata,
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"},
            ],
            "allow_free_text": True,
        }
        emit("lead_completed", lead="dynamic_executor", no_op=True)
        return state

    result: dict = {
        "storage_ref": latest_storage_ref,
        "produced_by_specialist": last_completed_specialist or plan[-1].get("specialist", "dynamic_executor"),
        "element_type": _ELEMENT_TYPE_BY_TOOL.get(
            latest_tool, brief.get("latest_element_type", "image")
        ),
        "metadata": {"dynamic_plan": True, "tool_used": latest_tool, **all_metadata},
        "extra_elements": extra_elements,
    }
    
    # If this was an edit plan on an existing element, update it. If it generated something new, don't.
    has_generator = any(s.get("specialist") in ("illustrator", "camera_director", "environment_designer") for s in plan)
    if brief.get("latest_element_id") and not has_generator and latest_tool not in _ANNOTATION_ONLY_TOOLS:
        result["update_existing_element_id"] = brief["latest_element_id"]
        
    state["result"] = result
    emit("lead_completed", lead="dynamic_executor")
    return state


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("ideation", run_ideation)
    graph.add_node("orchestrator", route)
    graph.add_node("visual_design_lead", _visual_design_lead_node)
    graph.add_node("motion_lead_pipeline", _motion_lead_node)
    graph.add_node("full_audio", _full_audio_node)
    graph.add_node("direct_fix", _direct_fix_node)
    graph.add_node("dynamic_executor", _dynamic_executor_node)

    graph.set_entry_point("ideation")
    graph.add_conditional_edges(
        "ideation",
        lambda s: "still_ideating" if s.get("result") is not None else "ready",
        {"still_ideating": END, "ready": "orchestrator"},
    )
    graph.add_conditional_edges(
        "orchestrator",
        route_condition,
        {
            "approval_required": END,
            "dynamic": "dynamic_executor",
            "full_image": "visual_design_lead",
            "full_video": "motion_lead_pipeline",
            "full_audio": "full_audio",
            "direct_fix": "direct_fix",
        },
    )
    graph.add_edge("dynamic_executor", END)
    graph.add_edge("visual_design_lead", END)
    graph.add_edge("motion_lead_pipeline", END)
    graph.add_edge("full_audio", END)
    graph.add_edge("direct_fix", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
        log.info("graph_compiled")
    return _compiled_graph
