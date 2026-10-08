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
from ...core.element_context import get_verified_image_description
from ...core.element_descriptions import NO_DESCRIPTION_SENTINEL
from ...core.events import emit
from ...core.exceptions import SpecialistFailed, SpecialistNeedsClarification, SpecialistNotFound
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..ideation.ideation_service import run_ideation
from ..leads.base import available_context_block
from ..specialists.registry import SPECIALIST_REGISTRY, get_specialist
from ..specialists.runner import run_concurrent_specialists, run_specialist_with_review
from .orchestrator import route, route_condition
from .state import GraphState


def _user_safe_failure_message() -> str:
    """Chat-facing failure text (Part 7, fidelity audit 2026-10-05): the raw `exc.message` (and the
    internal lead/specialist name) used to be interpolated straight into the chat bubble — a real,
    live-found example leaked a provider name and an environment-variable name verbatim ("Provider
    'replicate_llm' is unavailable: REPLICATE_API_TOKEN is not set"), and even the non-leaky cases
    named internal plumbing ("Ran into an issue with visual_design_lead") a non-technical user has
    no reason to see. The real detail is already logged server-side at every call site
    (`log.error(..., extra={"_extra_error": exc.message})`) right before this is built."""
    return "Ran into an issue generating that — want to try again?"


async def _emit_intermediate_element(
    session_id: str,
    element_type: str,
    produced_by_specialist: str,
    storage_ref: str,
    metadata: dict | None = None,
    product_id: str | None = None,
    parent_element_id: str | None = None,
):
    import asyncio
    import uuid

    from ...models.base import async_session_factory
    from ...repositories.models import CanvasElementModel
    from ...repositories.postgres.postgres_canvas_repository import PostgresCanvasRepository
    from .session_service import _run_compliance_background

    async with async_session_factory() as db:
        canvas = PostgresCanvasRepository(db)
        el = await canvas.add_element(CanvasElementModel(
            id=uuid.uuid4().hex,
            session_id=session_id,
            element_type=element_type,
            produced_by_specialist=produced_by_specialist,
            storage_ref=storage_ref,
            metadata_json=metadata or {},
            product_id=product_id,
            parent_element_id=parent_element_id,
        ))
    emit("element_created", element_id=el.id)
    asyncio.create_task(_run_compliance_background(el.id))

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
    "photorealistic_image_generator": "image",
    "high_resolution_image_generator": "image",
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
    {"composition_artist", "prop_stylist", "lighting_designer", "overlay_artist", "brand_asset_applier"}
)

# A dynamic plan step's real JSON output fields worth surfacing to LATER steps in the same plan
# (2026-10-06, prompt-engineering cross-check fix) — never the noisy/large fields (image_prompt,
# storage refs, raw tool payloads), just the actual decisions a later specialist should build on
# rather than silently re-derive or contradict: calibrated tone/voice, chosen palette, curated
# references, the approved headline (so caption_writer stays consistent with it), the shot list,
# and any claims/compliance notes already surfaced.
_PLAN_CONTEXT_WORTHY_FIELDS = frozenset(
    {
        "tone_profile", "voice_guidelines", "target_segment",
        "color_palette", "reference_summary",
        "primary_headline", "alternative_headlines", "hook_strategy",
        "overall_story", "shots", "pacing_target",
        "verified", "flagged_claims", "verification_notes",
    }
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
    context_parts = [f"User request:\n{state.get('user_message', '')}"]
    if brief.get("idea"):
        context_parts.append(f"Campaign idea so far:\n{brief['idea']}")
    referenced_elements = brief.get("referenced_elements_context", [])
    if referenced_elements:
        if len(referenced_elements) == 1 and not brief.get("_element_disambiguation_needed"):
            context_parts.append(
                "The following single element is the definitive target for your action. "
                "You MUST act on this exact element and DO NOT ask for clarification about which element to edit:"
            )
        else:
            context_parts.append("The following existing generated elements are available to reference or fix:")
        for i, el in enumerate(referenced_elements, 1):
            ref = el.get("storage_ref")
            kind = el.get("element_type", "unknown")
            # Real, live-found gap (2026-09-30): the recorded `description` is a generation
            # prompt — what was ASKED for, not necessarily what was actually delivered. A real
            # vision call (Replicate's Gemini 2.5 Flash) grounds this in what's ACTUALLY in the
            # image whenever one's available; falls back to the recorded description on any
            # vision failure, same as before this existed.
            verified_desc = await get_verified_image_description(el)
            desc = verified_desc or el.get("description") or NO_DESCRIPTION_SENTINEL
            context_parts.append(
                f"Element {i} (storage_ref: {ref}, type: {kind}) depicts:\n{desc}"
            )
        context_parts.append(
            "Act on the relevant existing asset(s), keeping everything the same except what the user's "
            "request asks to change — do not generate an unrelated new one."
        )

    direct_fix_context = "\n\n".join(context_parts)
    direct_fix_context += available_context_block(brief)
    from ..specialists.runner import deliverable_hint_block
    hint = deliverable_hint_block(brief)
    if hint:
        direct_fix_context += hint

    # Resuming a paused clarification question (2026-09-30) — reuse the EXACT context this
    # specialist saw when it asked, plus the user's real answer, instead of rebuilding fresh
    # context that has no memory of what was actually asked.
    resume_context = brief.get("_resume_direct_fix_context")
    if resume_context and brief.get("clarification_answer"):
        direct_fix_context = (
            f"{resume_context}\n\nThe user's answer to your clarifying question:\n"
            f"{brief['clarification_answer']}\n"
            "Use this to resolve the ambiguity and proceed — do not ask the same question again."
        )

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
            # Real, live-found bug (2026-10-06): every sibling call site passes `brief=brief` so
            # `run_specialist_agentic`'s tool-execution loop can build a real `tool_context`
            # (user_id/product_id/session_id) for `product_lookup`/`data_concierge`/
            # `brand_kit_lookup` — this one didn't. With no brief, those tools always got
            # `context=None` and correctly reported "not configured" regardless of what was
            # actually onboarded, silently defeating grounding for the entire direct_fix route
            # (confirmed live: "add a 15% discount" asked the user for the price that was already
            # in Product DNA).
            brief=brief,
        )
    except SpecialistFailed as exc:
        log.error("direct_fix_failed", extra={"_extra_specialist": target, "_extra_error": exc.message})
        state["result"] = {
            "message": _user_safe_failure_message(),
            "options": [
                {"id": "retry", "label": "Try again", "description": "Have the agent take another pass at it"},
                {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"}
            ],
            "allow_free_text": True
        }
        emit("lead_failed", lead="direct_fix", reason=exc.message)
        return state
    except SpecialistNeedsClarification as exc:
        # Real, live-found gap (2026-09-30): a genuine question used to collapse into the same
        # generic "Ran into an issue — retry/cancel" the except-SpecialistFailed branch above
        # produces — this surfaces the specialist's OWN real question/options instead, and
        # persists enough state (session_service.py) for the SAME specialist call to resume with
        # the user's real answer, rather than the turn just ending and restarting from scratch.
        log.info("direct_fix_needs_clarification", extra={"_extra_specialist": target, "_extra_question": exc.question})
        state["paused_plan"] = {
            "route": "direct_fix",
            "specialist_name": target,
            "context": direct_fix_context,
            "question": exc.question,
            "options": exc.options,
        }
        state["result"] = {
            "message": exc.question,
            "options": exc.options or [],
            "allow_free_text": exc.allow_free_text,
        }
        emit("lead_paused", lead="direct_fix", question=exc.question)
        return state

    produced_ref, produced_tool = _produced_ref(step)

    if not produced_ref:
        # Real, live-found gap (2026-09-29, adding the Copy Lead specialists): a lookup-only
        # specialist (caption_writer, headline_writer, tone_calibrator, copy_claims_checker, and —
        # a pre-existing, previously-unexercised instance of the same gap — reference_curator/
        # palette_strategist) can NEVER produce a storage_ref no matter how successfully it ran —
        # its real answer IS the JSON itself. Without this check, a genuinely complete result fell
        # through to the "wasn't confident enough" branch below, discarding it. Gated on
        # `is_lookup_only` (SpecialistSpec.allowed_tools all in the explicit lookup allowlist) so
        # this can never change behavior for a specialist with a real asset-producing tool
        # (composition_artist/overlay_artist/etc. declining an edit still hits the branch below,
        # unchanged) — `runner.py` already guarantees every `required_output_fields` key is present
        # by the time a result reaches here, re-checked directly rather than assumed.
        spec = get_specialist(target)
        if spec.is_lookup_only and all(f in step.data for f in spec.required_output_fields):
            state["result"] = {"message": spec.format_result_as_text(step.data), "specialist_notes": step.data}
            emit("lead_completed", lead="direct_fix", no_op=False)
            return state

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

    # Real, live-found gap (2026-10-05 fidelity audit): a script_writer result used to land as a
    # raw field dump with no canvas card and no way forward but "Try again/Cancel" — now that it
    # writes a real text_card_storage_ref (above), it reaches this success path like any other
    # asset, but still needs its own next-step options (a lookup/text-only result has nothing else
    # to react to) and to persist the approved script so `narrative_lead` can reuse it instead of
    # regenerating when the user picks "Turn this into a video".
    if target == "script_writer" and step.data.get("script_line"):
        brief["approved_script"] = step.data["script_line"]
        state["brief"] = brief
        result["message"] = "Here's the script — want to turn it into a video, revise it, or is this good as-is?"
        result["options"] = [
            {"id": "turn_into_video", "label": "Turn this into a video", "description": "Use this script as the voiceover for a full video generation"},
            {"id": "revise_script", "label": "Revise the script", "description": "Say what to change and I'll rewrite it"},
            {"id": "looks_good", "label": "Looks good", "description": "Keep it as-is for now"},
        ]
        result["allow_free_text"] = True

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

    current_context = [{"type": "text", "text": f"Campaign idea so far:\n{brief.get('idea') or user_message}"}]

    # Real, live-found ordering fix (2026-09-26, decomposition-quality investigation): the full
    # A raw brief JSON dump used to go here, AFTER the referenced-element context and BEFORE the
    # aspect-ratio hint — burying the actual actionable instructions behind the biggest, noisiest
    # block right before the model has to act. `available_context_block` (2026-10-05, fidelity
    # audit) replaces it with a short, structured summary instead — concise regardless of position,
    # so where it sits in the message matters far less than when this was a full JSON wall.
    current_context[0]["text"] += available_context_block(brief)

    referenced_elements = brief.get("referenced_elements_context", [])
    if referenced_elements:
        # Real, live-found gap (2026-09-30): this used to call `build_element_context_blocks`
        # (Fix 8, 2026-09-26) to attach a real `image_url` content block alongside text — but
        # EVERY real specialist call routes through `groq.py`, which passes `strip_images=True`
        # unconditionally, so that image block was always silently discarded before the request
        # ever reached the model. Dead weight since it shipped: real base64 payload/tokens built
        # and sent for nothing. Replaced with `get_verified_image_description` — a real vision
        # call (Replicate's Gemini 2.5 Flash) made ONCE, grounding the TEXT description in what's
        # actually in the image, the same pattern `_direct_fix_node` now uses too.
        if len(referenced_elements) == 1 and not brief.get("_element_disambiguation_needed"):
            current_context.append({
                "type": "text",
                "text": (
                    "The following single element is the definitive target for your action. "
                    "You MUST act on this exact element and DO NOT ask for clarification about which element to edit:"
                )
            })
        else:
            current_context.append({
                "type": "text",
                "text": "The following existing generated elements are available to reference or fix:",
            })
        for i, el in enumerate(referenced_elements, 1):
            ref = el.get("storage_ref")
            kind = el.get("element_type", "unknown")
            verified_desc = await get_verified_image_description(el)
            desc = verified_desc or el.get("description") or NO_DESCRIPTION_SENTINEL
            current_context.append({
                "type": "text",
                "text": (
                    f"Element {i} (storage_ref: {ref}, type: {kind}) depicts:\n{desc}\n"
                    "(Note: If generating a new visual base from this, pass this storage_ref as "
                    "'reference_storage_ref' to base_image_generator)"
                ),
            })

    # Real, live-found bug (2026-10-07, Monster Energy "1:1 vs got 16:9" complaint): the aspect-
    # ratio backstop hint used to be computed ONCE here, from the whole turn's message, and copied
    # unchanged into every step's context via `current_context` below — so a step whose own
    # instruction said "1:1" still got a different, turn-wide "set aspect_ratio to 16:9" hint if
    # some OTHER part of the same campaign message named a 16:9 deliverable. Moved into
    # `_run_one_step` (below) so each step's hint is scoped to THAT STEP'S OWN instruction, never
    # the whole turn's — see `deliverable_hint_block`'s own docstring in `runner.py` for the full
    # trace.

    # Real, live-found gap (2026-09-30): resuming a paused plan (session_service.py detected
    # `session.brief["paused_plan"]`) must NOT re-run the steps that already genuinely completed
    # — that would re-pay for/re-invoke every specialist before the pause, discarding real,
    # already-produced results. `resume_from_step_index`/`resume_completed_results` (set by
    # session_service.py from the persisted paused_plan) seed this run's starting point instead of
    # the usual empty state.
    resume_from_step_index = brief.get("_resume_next_step_index")
    resume_completed = brief.get("_resume_completed_results") or {}
    latest_storage_ref = resume_completed.get("latest_storage_ref")
    latest_tool = resume_completed.get("latest_tool")
    last_completed_specialist = resume_completed.get("last_completed_specialist")
    all_metadata = dict(resume_completed.get("all_metadata") or {})
    extra_elements = list(resume_completed.get("extra_elements") or [])

    if brief.get("clarification_answer"):
        current_context.append({
            "type": "text",
            "text": (
                "The user's answer to the clarifying question you (or a prior step) asked:\n"
                f"{brief['clarification_answer']}\n"
                "Use this to resolve the ambiguity — do not ask the same question again."
            ),
        })

    # Only enforce asset generation for specialists that actually produce assets, not for
    # planning/strategy specialists (like reference_curator or palette_strategist). Real, live-
    # found bug (2026-10-05, found while verifying camera_director's reliability as a standalone
    # 'dynamic' step for the video-thumbnail-equivalent fix): this set previously contained TOOL
    # names and invented names that are never real specialists at all (`base_image_generator`,
    # `image_animator`, `upscaler`, `outpainter` — none of these match `SPECIALIST_REGISTRY`), so
    # `specialist in generating_specialists` below could never match the two specialists it most
    # needed to (`illustrator`, `camera_director`) — a real image/video generation that silently
    # produced no asset never triggered the self-correction retry this check exists for. Fixed to
    # the real, registered specialist names that call an asset-producing tool.
    # `environment_designer`/`prop_stylist`/`lighting_designer` added on cross-check (same day):
    # all three call `image_editor`/`base_image_generator` same as `composition_artist`/
    # `scene_builder` (already in this set) and were missed in the first pass.
    generating_specialists = {
        "illustrator", "composition_artist", "camera_director", "video_editor_cutter",
        "sound_designer", "overlay_artist", "scene_builder", "brand_asset_applier",
        "environment_designer", "prop_stylist", "lighting_designer",
    }

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

        # Deterministic aspect-ratio backstop (2026-09-25), scoped per-step (2026-10-07 fix — see
        # the comment above this closure and `deliverable_hint_block`'s own docstring): pass THIS
        # step's own instruction text, never the whole turn's message, so a step that says "1:1"
        # never sees a hint derived from some OTHER step's "16:9" deliverable in the same plan.
        from ..specialists.runner import deliverable_hint_block
        hint = deliverable_hint_block(brief, step_text=instruction)
        if hint:
            instruction_text += hint

        # Real, live-found bug (2026-10-06, prompt-engineering cross-check): only `storage_ref`
        # was ever threaded between dynamic-plan steps — a planning/strategy specialist's real
        # JSON decision (tone_calibrator's `tone_profile`/`voice_guidelines`, palette_strategist's
        # `color_palette`, headline_writer's `primary_headline`) was computed, stored in
        # `all_metadata`, and then never actually shown to any LATER step — e.g. a calibrated
        # brand voice had zero influence on the headline/caption writers that followed it in the
        # same plan, even though they ran in that exact order on purpose. Surface every earlier
        # step's informational fields here so later steps can genuinely use (not re-derive or
        # contradict) decisions already made in this same plan.
        prior_notes = []
        for step_key in sorted(all_metadata.keys()):
            worthy = {k: v for k, v in all_metadata[step_key].items() if k in _PLAN_CONTEXT_WORTHY_FIELDS and v}
            if worthy:
                prior_notes.append(f"- {step_key}: {json.dumps(worthy, ensure_ascii=False)}")
        if prior_notes:
            instruction_text += (
                "\n\nReal decisions already made by earlier steps in this plan — use them, do not "
                "re-derive or contradict them:\n" + "\n".join(prior_notes)
            )

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
        try:
            return await run_specialist_with_review(
                specialist,
                context=step_context,
                needs_retry=lambda r, specialist=specialist: (
                    specialist in generating_specialists and _produced_ref(r)[0] is None
                ),
                reminder="REMINDER: You must call a tool to fulfill your instruction and produce an asset.",
                brief=brief,
            )
        except SpecialistNeedsClarification as exc:
            # Tags exactly which plan step (this closure's own `i`) raised the question — the
            # outer loop's except block needs this to compute `next_step_index` for resumption,
            # and it's otherwise lost once the exception propagates past this closure's own scope
            # (through `run_concurrent_specialists`'s re-raise for a parallel group, or straight up
            # the sequential for-loop either way).
            exc.step_index = i
            exc.specialist_name_at_step = specialist
            raise

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
            if resume_from_step_index is not None and all(gi < resume_from_step_index for gi in group_indices):
                # Already genuinely completed before the pause — its real result is already
                # folded into `all_metadata`/`latest_storage_ref` above via `resume_completed`.
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
                elif specialist in generating_specialists:
                    # Real, live-found bug (2026-10-07, Monster Energy "SALE" overlay complaint):
                    # a generating specialist (illustrator here) can exhaust its retry
                    # (`needs_retry` above) and still never call a real asset-producing tool —
                    # only lookup tools (product_lookup/brand_kit_lookup), confirmed via live log
                    # trace. Before this check, the loop just silently kept whatever
                    # `latest_storage_ref` already held (here: nothing from THIS plan, so the
                    # NEXT step fell back to the stale pre-turn referenced element already in
                    # context) and continued as if nothing went wrong — the next step
                    # (overlay_artist) then drew text onto that old, unrelated image, producing
                    # exactly the "pasted onto a broken base image" result reported live. Mirrors
                    # `_direct_fix_node`'s existing, already-correct "wasn't confident enough"
                    # honest-failure path (same file, ~line 1297) instead of silently continuing.
                    spec = get_specialist(specialist)
                    if not (spec.is_lookup_only and all(f in step_result.data for f in spec.required_output_fields)):
                        specialist_intent = _describe_specialist_intent(step_result.data)
                        raise SpecialistFailed(
                            specialist,
                            f"{specialist} considered this"
                            f"{f' — it was thinking: {specialist_intent}' if specialist_intent else ''}, "
                            f"but wasn't confident enough to actually produce an asset for this step. "
                            f"Could you say more specifically what you'd like so it can act on it directly?",
                        )

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
    except SpecialistNeedsClarification as exc:
        # Real, live-found gap (2026-09-30): a genuine question mid-plan used to be indistinguishable
        # from a crash — this stops the loop (no further steps run) and persists exactly enough state
        # (session_service.py writes this into session.brief["paused_plan"]) to resume at THIS
        # step, reusing every already-completed step's real result, rather than restarting the
        # whole plan from step 1.
        step_index = getattr(exc, "step_index", 0)
        log.info(
            "dynamic_plan_needs_clarification",
            extra={
                "_extra_specialist": getattr(exc, "specialist_name_at_step", exc.specialist_name),
                "_extra_step_index": step_index, "_extra_question": exc.question,
            },
        )
        state["paused_plan"] = {
            "route": "dynamic",
            "plan": plan,
            "next_step_index": step_index,
            "completed_results": {
                "all_metadata": all_metadata,
                "latest_storage_ref": latest_storage_ref,
                "latest_tool": latest_tool,
                "last_completed_specialist": last_completed_specialist,
                "extra_elements": extra_elements,
            },
            "question": exc.question,
            "options": exc.options,
        }
        state["result"] = {
            "message": exc.question,
            "options": exc.options or [],
            "allow_free_text": exc.allow_free_text,
        }
        emit("lead_paused", lead="dynamic_executor", question=exc.question, step_index=step_index)
        return state
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

        # Real, live-found gap (2026-09-30, same root cause `_direct_fix_node` was already fixed
        # for): a dynamic plan made entirely of lookup-only specialists (caption_writer,
        # headline_writer, tone_calibrator, copy_claims_checker — no asset-producing tool, by
        # design) can never set `latest_storage_ref`, so their genuinely complete result used to
        # fall into this same "did not produce any visible assets" message as an outright failure
        # — discarding a real caption/headline the plan actually wrote. Mirrors
        # `_direct_fix_node`'s `is_lookup_only`/`format_result_as_text` fix: if every step that ran
        # was lookup-only and none reported an error, render their real results as the message
        # instead of a fabricated "nothing happened".
        lookup_only_text = None
        if specialist_error is None:
            rendered: list[str] = []
            all_lookup_only = True
            for key, meta in all_metadata.items():
                if key == "partial_failure" or not isinstance(meta, dict):
                    continue
                specialist_name = key.split("_", 2)[-1] if key.count("_") >= 2 else None
                spec = SPECIALIST_REGISTRY.get(specialist_name) if specialist_name else None
                if spec is None or not spec.is_lookup_only:
                    all_lookup_only = False
                    break
                rendered.append(spec.format_result_as_text(meta))
            if all_lookup_only and rendered:
                lookup_only_text = "\n\n".join(rendered)

        state["result"] = {
            "message": specialist_error or lookup_only_text or "The plan executed but did not produce any visible assets.",
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
    has_generator = any(s.get("specialist") in ("illustrator", "camera_director", "environment_designer", "scene_builder") for s in plan)
    if brief.get("latest_element_id") and not has_generator and latest_tool not in _ANNOTATION_ONLY_TOOLS:
        result["update_existing_element_id"] = brief["latest_element_id"]
        
    state["result"] = result
    emit("lead_completed", lead="dynamic_executor")
    return state


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("ideation", run_ideation)
    graph.add_node("orchestrator", route)
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
            "plan_approval": END,
            "dynamic": "dynamic_executor",
            "direct_fix": "direct_fix",
        },
    )
    graph.add_edge("dynamic_executor", END)
    graph.add_edge("direct_fix", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
        log.info("graph_compiled")
    return _compiled_graph
