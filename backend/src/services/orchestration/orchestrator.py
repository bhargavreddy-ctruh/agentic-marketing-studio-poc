"""
The Orchestrator — four routes. The first three are per Architecture.md section 1 (transcribed
from the source PDF's Section 7):

    1. Full still image  -> Visual Design Lead (+ Scene Lead if a background/scene is needed)
    2. Full video        -> Narrative Lead -> Scene Lead -> Motion Lead
    3. Single-element fix -> direct specialist call, bypassing its Lead

A 4th route, "full_audio", was added 2026-09-22 — a real, disclosed extension beyond the reference
PDF's own three, not a silent deviation (genai_build's ADR spirit: written down here, not snuck
in). Real gap found live: the canvas's right-click "New Audio" had nothing to route to — Sound
Designer only ever ran via "direct_fix", which the orchestrator's own prompt correctly refuses
unless an existing element is already on the canvas, so a brand-new standalone audio request (no
prior image/video yet) had no route at all. Sound Designer's own prompt (sound_designer.md)
already never depended on an existing element's storage_ref, only campaign/shot context — so this
is a real, honest capability that already existed, just unreachable. Disclosed limit: Sound
Designer can only produce a spoken VOICEOVER line via `text_to_speech` — "there is still no
music-generation capability in this build" per its own prompt — so "full_audio" produces real
speech audio, never music, regardless of what the user asked for.

Real Tier-1-model-driven classification (Memory.md, Phase 3 conformance audit) — the Phase 0
keyword heuristic this replaces stayed in place well past the phase that was supposed to upgrade
it; PRD.md/Architecture.md both describe real classification as the intended behavior, not a
keyword match. A cheap, fast Tier 1 model call decides the route and, for "direct_fix", which
single specialist to call — genuinely reasoning about the request, not substring-matching it.

The old keyword heuristic is kept as a fallback ONLY for when the LLM call itself is unavailable
(both OpenRouter and Groq down) — never silently defaulting to a wrong route without trying the
real classification first (Rules.md section 2).
"""
from __future__ import annotations

from ...core.chat_history import build_history_messages
from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..specialists.registry import SPECIALIST_REGISTRY
from .specialist_classifier import describe_specialists
from .state import GraphState

log = get_logger(__name__)

_VIDEO_KEYWORDS = ("video", "clip", "motion", "reel")
_AUDIO_KEYWORDS = ("audio", "voiceover", "voice over", "narration", "spoken", "sound clip")
_FIX_KEYWORDS = ("fix", "just change", "just fix", "relight", "recolor", "redo", "update", "remove", "add")

_VALID_ROUTES = {"dynamic", "full_image", "full_video", "full_audio", "direct_fix"}

_SYSTEM_PROMPT = """<role>
You are the Orchestrator for a creative marketing studio. Your job is to analyze the user's request and dynamically assemble a plan of specialists to execute it.
</role>

<rules>
1. **Dynamic Assembly:** You do NOT use hardcoded pipelines. Instead, you select EXACTLY the specialists needed to fulfill the request, in the exact order they should run, and provide a clear instruction for each step.
2. **Efficiency & Autonomy:** Do not waste steps, but DO autonomously include planning and strategy specialists (like 'reference_curator' or 'palette_strategist') if the task is complex, broad, or requires a cohesive brand style (e.g., a "campaign" or "brand refresh"). Do not rely on the user to explicitly ask for them.
3. **Valid Specialists Only:** You can only use the specialists listed below.
4. **Base Decision on Latest Request:** The user's most recent message is the primary driver of intent.
5. **Campaign Defaults (CRAZY & BOLD):** We are making this for elite marketing and creating campaigns. Image and video generations should be CRAZY, striking, and visually incredible. If the request is for a broad "campaign", autonomously build a robust plan (e.g. style/palette planning, generating 1-2 base images via illustrator, and applying promotional text via overlay_artist). Push the creative boundaries.
6. **Context Guardrail:** If the request and the brief entirely lack a specific subject or product (e.g., the user just says "retry" but there is no product established), do NOT invent or guess a generic product. Instead, return a plan instructing the first specialist to fail and ask the user for clarification.
7. **Editing Existing Assets:** If the user request is to modify, fix, or edit an existing referenced element (e.g., "edit this image", "strike out the price", "change the color"), you MUST use route 'direct_fix' and provide the 'target_specialist'. Do NOT use 'dynamic' for edits on existing assets. For image content edits (recoloring, changing subjects, adding/removing visual elements, modifying the image itself), use 'composition_artist'. Only use 'overlay_artist' for pure TEXT overlays (adding price tags, discount labels, promotional text ON TOP of an image). This applies to 'dynamic' plans too: if a referenced element exists and the request is an edit to it, NEVER put a from-scratch generator (e.g. 'illustrator') as a plan step — that discards the referenced element and produces an unrelated new asset instead of the edit the user asked for. Either route 'direct_fix' to 'composition_artist' (preferred for a single edit), or, only if the request genuinely needs multiple steps, make the first 'dynamic' step an editing-capable specialist operating on the referenced element's storage_ref.
8. **Cross-Referencing & Memory:** You will be provided with retrieved long-term memory and multiple referenced elements if applicable. Use this history and cross-reference information to build highly accurate 'dynamic' plans or pick the right 'direct_fix' specialist.
</rules>

<specialists>
{specialist_descriptions}
</specialists>

<output_format>
Return ONLY valid JSON matching this schema:
{{
  "route": "Must be exactly one of: 'dynamic', 'direct_fix', 'full_video', 'full_image', 'full_audio'",
  "target_specialist": "Required ONLY IF route is 'direct_fix'. MUST be a valid specialist name from the <specialists> section (e.g., 'overlay_artist', 'composition_artist'). DO NOT return null if route is 'direct_fix'. Otherwise null.",
  "plan": [ // Required ONLY IF route is 'dynamic'. Otherwise null.
    {{
      "specialist": "the exact name of the specialist",
      "instruction": "Clear instruction for what this specialist needs to accomplish in this step"
    }}
  ]
}}
</output_format>
"""


def _keyword_fallback_route(message: str) -> tuple[str, str | None, list[dict] | None]:
    """The original Phase 0 heuristic — used only if the real LLM classification call itself
    fails, never as the default path. `_AUDIO_KEYWORDS` added 2026-09-22 alongside "full_audio" —
    checked before the video keywords since "voiceover"/"narration" alone (no "video"/"clip"
    keyword present) should degrade to audio, not video."""
    if any(k in message for k in _FIX_KEYWORDS):
        return "direct_fix", "composition_artist", None
    if any(k in message for k in _AUDIO_KEYWORDS) and not any(k in message for k in _VIDEO_KEYWORDS):
        return "full_audio", None, None
    if any(k in message for k in _VIDEO_KEYWORDS):
        return "full_video", None, None
        
    # Default to a basic dynamic generation plan instead of the old, static full_image pipeline
    return "dynamic", None, [
        {"specialist": "illustrator", "instruction": "Generate the requested image based on the prompt."}
    ]

@traceable(name="orchestrator_node")
async def route(state: GraphState) -> GraphState:
    # Resuming a paused "approve" mode video pipeline (Memory.md, Phase 4) — always route straight
    # back to the video pipeline node, which itself interprets the message as approve/revise.
    # Explicit state tracking, not LLM classification, decides this — reliable and free, since
    # there's nothing genuinely ambiguous to classify once video_stage is set.
    brief_stage_check = state.get("brief") or {}
    if brief_stage_check.get("video_stage") in ("narrative_pending", "scene_pending", "motion_pending"):
        state["route"] = "full_video"
        state["target_specialist"] = None
        # No LLM call on this resume path, but still stripped for the same reason as the main
        # return below — leads/specialists downstream don't need it restated in their own dumps.
        if "_recent_chat_history" in brief_stage_check:
            state["brief"] = {k: v for k, v in brief_stage_check.items() if k != "_recent_chat_history"}
        emit("route_decided", route="full_video", target_specialist=None, resumed=True)
        return state

    # Resuming after a real, explicit user approval of Laya's fallback suggestion (2026-09-23,
    # per a real user report: "Cancel is also creating llm task??" — the same fallthrough bug also
    # meant "Yes, use X" never actually ran X, it just re-classified the approval text). Set once,
    # consumed once, by `session_service.py::post_turn`'s Laya-fallback-approval handling — skips
    # real classification entirely and goes straight to `direct_fix` with the specialist the user
    # already explicitly approved, same "explicit state beats reclassifying" shape as the
    # `video_stage` resume-check just above. Not a routing override of a MODEL's decision (the
    # thing reverted earlier today) — this is honoring the user's OWN explicit choice, a different
    # thing entirely.
    laya_approved = brief_stage_check.get("_laya_approved_specialist")
    if laya_approved in SPECIALIST_REGISTRY:
        state["route"] = "direct_fix"
        state["target_specialist"] = laya_approved
        state["dynamic_plan"] = None
        emit("route_decided", route="direct_fix", target_specialist=laya_approved, resumed=True)
        return state

    # Both the raw message AND the merged brief are given to the classifier — a merged brief alone
    # loses "just fix X" framing once ideation paraphrases it into plain descriptive language
    # (Memory.md, Phase 3 conformance audit: a real bug found live this way — routing off the
    # merged brief alone sent an explicit "just fix the lighting" request through full
    # regeneration instead of direct_fix).
    brief = state.get("brief") or {}
    brief_idea = brief.get("idea") or ""
    user_message = state.get("user_message") or ""
    referenced_elements = brief.get("referenced_elements_context", [])
    
    classification_context = [f"User's most recent literal message:\n{user_message or '(none)'}"]
    if brief_idea:
        classification_context.append(f"Merged brief so far:\n{brief_idea}")
        
    retrieved_memory = brief.get("_retrieved_memory")
    if retrieved_memory:
        classification_context.append(f"Relevant historical chat memory:\n{retrieved_memory}")
    
    if referenced_elements:
        elements_desc = []
        for i, el in enumerate(referenced_elements, 1):
            kind = el.get("element_type", "unknown kind")
            desc = el.get("description", "(no description recorded)")
            elements_desc.append(f"Element {i} (Type: {kind}): {desc}")
            
        elements_str = "\n".join(elements_desc)
        classification_context.append(
            f"The following existing generated elements ARE available to fix:\n{elements_str}\n"
            f"Compare this against the user's most recent message: if the message is asking for "
            f"something about a DIFFERENT subject/kind than what these existing elements actually "
            f"are (e.g. the existing element is a logo and the new message asks for a car photo "
            f"unrelated to any logo), that is a NEW/DIFFERENT asset request, never direct_fix on "
            f"these elements — route to whichever full_* route matches what's actually being asked "
            f"for instead.\n"
            f"If instead the message is asking to edit/modify/fix/add something ON one of these "
            f"same elements (a price tag, a discount, a color change, a text strike-out, etc.), "
            f"you MUST ground your plan in that element: route 'direct_fix' to 'composition_artist' "
            f"(or 'overlay_artist' only for a pure text overlay), never route 'dynamic' with a "
            f"from-scratch generator like 'illustrator' — that would throw away this exact element "
            f"and produce an unrelated new one instead of the edit being asked for."
        )
    else:
        classification_context.append("An existing generated element is available to fix: no")

    llm = get_llm_provider()
    
    from ...providers.llm.laya_provider import LayaProvider
    import asyncio
    
    async def _safe_laya_choice():
        try:
            return await LayaProvider.predict_choice(
                state=f"{brief_idea}\n\nUser request: {user_message}",
                options=list(_VALID_ROUTES)
            )
        except Exception:
            return None
            
    laya_task = asyncio.create_task(_safe_laya_choice())
    
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_SYSTEM_PROMPT.format(specialist_descriptions=describe_specialists()),
            messages=build_history_messages(brief, "\n\n".join(classification_context)),
            max_tokens=1536,
            prefer_local=False,
        )
        parsed = extract_json(result.text)
        chosen_route = str(parsed.get("route") or "")
        target_specialist = str(parsed.get("target_specialist") or "").strip() or None
        dynamic_plan = parsed.get("plan") or None

        if chosen_route not in _VALID_ROUTES:
            raise ValueError(f"model returned an invalid route: {chosen_route!r}")
        if chosen_route == "direct_fix" and target_specialist not in SPECIALIST_REGISTRY:
            # A real, named failure mode rather than silently accepting a hallucinated specialist
            # name — fall through to full_image rather than routing to something that doesn't exist.
            log.warning(
                "orchestrator_invalid_target_specialist",
                extra={"_extra_target": target_specialist},
            )
            chosen_route, target_specialist = "full_image", None

        # Real, live-found bug (2026-09-23): a referenced-element edit ("Edit image with offer
        # price of 15% on 30000...") got routed to 'dynamic' with an illustrator regeneration plan
        # instead of 'direct_fix'/composition_artist — Rule 7 already said not to do this, but a
        # real, live turn violated its own rule (classification fell back to a weaker local model
        # under today's well-documented real Groq/OpenRouter rate-limiting, per
        # `llm_router_falling_back_to_local_last_resort` in the real logs for this exact turn).
        # First fix attempt was a keyword-pattern override forcing the route in code — rejected on
        # review (2026-09-23): routing must stay model-driven, not hardcoded phrase-matching.
        # Reverted. Real fix is upstream, in the prompt itself: Rule 7 above now explicitly extends
        # to 'dynamic' plans (never a from-scratch generator when a referenced element should be
        # edited), and the referenced-elements block built above restates that requirement right
        # next to the actual element data, so even a weaker fallback model sees the constraint in
        # the same breath as what it's constraining. No deterministic route-rewrite here — the
        # model's own decision is trusted, same as every other route.
        _msg_lower = (user_message or "").lower()

        # Real, live-found bug (2026-09-23, a raw HTTP 500 in production): the `dynamic` route's
        # multi-step plan never got the same validation `direct_fix`'s single `target_specialist`
        # already has above — a hallucinated specialist name in ANY step (e.g. "style_board_planner",
        # which was never registered anywhere in this codebase) went straight into
        # `state["dynamic_plan"]` unchecked, then crashed `_dynamic_executor_node` with an uncaught
        # `SpecialistNotFound` deep inside its execution loop — a raw 500, not a graceful degrade.
        # Raising here routes it through the SAME already-tested fallback chain just below (Laya
        # recommends a real specialist with real user approval, then a keyword heuristic) rather
        # than inventing new, less-proven handling for a genuinely newer, less-hardened route.
        if chosen_route == "dynamic" and dynamic_plan:
            invalid_specialists = {
                str(step.get("specialist")) for step in dynamic_plan
                if step.get("specialist") not in SPECIALIST_REGISTRY
            }
            if invalid_specialists:
                raise ValueError(
                    f"model's dynamic plan named unregistered specialist(s): {sorted(invalid_specialists)!r}"
                )

        # Code-level safety net: if the user explicitly says "image edit" or "edit image" (or
        # similar patterns), the intent is a visual modification via image_editor, not a text
        # overlay. The LLM often confuses the two when the request mentions prices/discounts,
        # because overlay_artist's description historically attracted those keywords. This
        # deterministic check overrides the LLM when there's a clear mismatch. (`_msg_lower` is
        # computed just above.) Pre-existing (not part of the 2026-09-23 routing fix above); kept
        # as-is since it's a narrower same-route specialist correction, not a route override.
        _IMAGE_EDIT_PATTERNS = ("image edit", "edit image", "edit this image", "edit the image", "strike the", "strike out")
        if (
            chosen_route == "direct_fix"
            and target_specialist == "overlay_artist"
            and any(p in _msg_lower for p in _IMAGE_EDIT_PATTERNS)
        ):
            log.info(
                "orchestrator_override_overlay_to_composition",
                extra={"_extra_original": "overlay_artist", "_extra_override": "composition_artist"},
            )
            target_specialist = "composition_artist"
            
        if chosen_route == "dynamic" and not dynamic_plan:
            raise ValueError("model returned a dynamic route but an empty plan")

        state["route"] = chosen_route
        state["target_specialist"] = target_specialist
        state["dynamic_plan"] = dynamic_plan
        
        # Shadow mode mismatch check
        laya_route = await laya_task
        if laya_route and laya_route != chosen_route:
            log.warning(
                "orchestrator_route_mismatch", 
                extra={"_extra_llm_route": chosen_route, "_extra_laya_route": laya_route}
            )
            
        log.info(
            "orchestrator_routed",
            extra={"_extra_route": chosen_route, "_extra_target": target_specialist, "_extra_method": "llm"},
        )
        emit("route_decided", route=chosen_route, target_specialist=target_specialist)
    except (ProviderUnavailable, ValueError) as exc:
        # LLM gateways are down, or it returned something unparseable.
        # Try asking the Laya model to predict the specialist first.
        options_list = list(SPECIALIST_REGISTRY.keys())
        try:
            # We must recreate the task because laya_task might have been awaited and failed.
            laya_specialist = await LayaProvider.predict_choice(
                state=f"{brief_idea}\n\nUser request: {user_message}",
                options=options_list
            )
        except Exception:
            laya_specialist = None

        if laya_specialist and laya_specialist in SPECIALIST_REGISTRY:
            log.warning(
                "orchestrator_routing_laya_fallback",
                extra={"_extra_route": "approval_required", "_extra_laya_specialist": laya_specialist, "_extra_reason": str(exc)},
            )
            state["route"] = "approval_required"
            state["target_specialist"] = None
            state["dynamic_plan"] = None
            state["result"] = {
                "message": f"I couldn't confidently decide how to route this request, but my fallback model suggests routing this to **{laya_specialist}**. Do you want to proceed?",
                "options": [
                    {
                        "id": f"laya_approve_{laya_specialist}",
                        "label": f"Yes, use {laya_specialist}",
                        "description": SPECIALIST_REGISTRY[laya_specialist].description
                    },
                    {
                        "id": "cancel",
                        "label": "No, cancel",
                        "description": "Stop this task"
                    }
                ]
            }
            emit("route_decided", route="approval_required", target_specialist=laya_specialist, degraded=True)
        else:
            # Both LLM and Laya failed (or Laya couldn't pick) — degrade to the old heuristic.
            fallback_route, fallback_target, fallback_plan = _keyword_fallback_route((user_message or brief_idea).lower())
            state["route"] = fallback_route
            state["target_specialist"] = fallback_target
            state["dynamic_plan"] = fallback_plan
            log.warning(
                "orchestrator_routing_fallback",
                extra={"_extra_route": fallback_route, "_extra_reason": str(exc)},
            )
            emit("route_decided", route=fallback_route, target_specialist=None, degraded=True)

    # `_recent_chat_history` and `_retrieved_memory` were real, useful context for THIS classification 
    # call (and ideation's own calls before it). The previous implementation stripped them to save tokens,
    # but this broke conversational continuations (like 'go ahead' or 'yes') when a specialist failed.
    # We now retain them so downstream specialists have the conversational context to understand overrides.
    for scratch_key in ("_retrieved_memory",): # Kept stripping memory to save space, but kept chat history
        if scratch_key in (state.get("brief") or {}):
            state["brief"] = {k: v for k, v in state["brief"].items() if k != scratch_key}

    return state


def route_condition(state: GraphState) -> str:
    """The conditional-edge selector LangGraph calls after `route` runs."""
    return state.get("route") or "dynamic"
