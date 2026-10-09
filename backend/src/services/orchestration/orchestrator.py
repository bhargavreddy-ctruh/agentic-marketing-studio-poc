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


def _build_plan_preview(
    chosen_route: str,
    target_specialist: str | None,
    dynamic_plan: list[dict] | None,
    user_message: str,
) -> list[dict]:
    """A normalized `{specialist, instruction, parallel_group}` list for EVERY route (2026-10-06,
    explicit user ask: "shows in chat box (like in luma)... then follow them parallely or
    sequentially"), emitted once right after a route is decided, purely informational — no
    execution logic anywhere is touched by this function or its caller.

    `dynamic` reuses its own real, LLM-authored, per-request plan verbatim — the only route with
    genuine `parallel_group` data. Every fixed-pipeline route reports its own real, declared
    `LeadSpec.specialist_sequence` (Architecture.md's own framing: "a Lead is just an ordered list
    of specialist names") as a SEQUENTIAL list — deliberately never claims a parallel grouping for
    these, since the real concurrency inside them (e.g. Narrative Lead's script_writer/
    pacing_editor running concurrently unless `approved_script` already exists) is conditional,
    code-driven control flow, not static data; a wrong parallel claim here would be actively
    misleading, whereas "sequential" is always a safe, correct (if coarser) statement."""
    if chosen_route == "dynamic" and dynamic_plan:
        return dynamic_plan
    if chosen_route == "direct_fix" and target_specialist:
        return [{"specialist": target_specialist, "instruction": user_message, "parallel_group": None}]
    return []

_FIX_KEYWORDS = ("fix", "just change", "just fix", "relight", "recolor", "redo", "update", "remove", "add")

_VALID_ROUTES = {"dynamic", "direct_fix"}

_SYSTEM_PROMPT = """<role>
You are the Orchestrator for a creative marketing studio. Your job is to analyze the user's request and dynamically assemble a plan of specialists to execute it.
</role>

<rules>
1. **Comprehensive Craft Plans (MINIMUM 4 STEPS — ZERO EXCEPTIONS):** A creative deliverable is NEVER just a raw, isolated generation. Every creative request (campaigns, social posts, hero banners, posters, product ads, festival promotions, videos) MUST produce a plan with AT LEAST 4 specialists:
   - **Step 1 — Creative Strategy:** `text_card_writer` to formulate the messaging angle, headline, promotional hooks, and value proposition.
   - **Step 2 — Visual Production:** `illustrator` (for images/visuals) or `camera_director` (for video).
   - **Step 3 — Graphic Design:** `overlay_artist` to apply promotional badges, discount tags, typography, or CTA banners.
   - **Step 4 — Marketing Copy:** `caption_writer` to generate compelling promotional copy, hashtags, and announcement text.
   A plan with FEWER THAN 4 steps for ANY creative request is WRONG and will be rejected. NEVER collapse any deliverable into fewer than 4 steps!
2. **Autonomous Leadership & Collaboration:** As an elite creative director, coordinate specialists so they build on each other's outputs.
3. **Valid Specialists Only:** You can only use the specialists listed below.
4. **Autonomous Creative Initiative:** If the user provides a reference image without explicit instructions, treat it as the product or visual anchor. Take decisive creative leadership and assemble a complete 4+ step plan.
5. **Marketing Standards (CRAZY & BOLD):** Visuals and marketing assets must be striking, vibrant, and commercial-grade. For every deliverable, include visual generation, graphic overlays/badges, and engaging copy. NEVER force or default to any single platform unless the user explicitly requested it.
5b. **Text on New Images — Flat Overlay vs. In-Scene Element:**
   - **Flat/graphic overlay** (badges, discount %, prices, CTA strips, sale text, headers): add `overlay_artist` as a dedicated step AFTER `illustrator`.
   - **In-scene elements** (neon signage, graffiti, ambient text painted into the 3D scene): instruct `illustrator` to include them directly in the scene prompt.
6. **Grounding & Scope:** Ground your plan in whatever product, brand, or creative hook is present.
6b. **Reference Image Handling:** If the user provided a reference image, explicitly instruct the generating specialist (e.g., `illustrator` for images or `camera_director` for video) to use it as its reference: "You MUST use the provided referenced element as your image-to-image reference_storage_ref".
7. **Editing Existing Assets vs. Creating New Deliverables:**
7a. **In-place edit** (the deliverable stays the SAME shot, just tweaking an existing asset): route 'direct_fix' with target specialist (`composition_artist` for visual changes, `overlay_artist` for text-only tweaks).
7b. **New Deliverable or Campaign using the element as reference material:** Route 'dynamic' with FULL 4-step pipeline:
   - Step 1: `text_card_writer` for creative concept, hook, and positioning.
   - Step 2: `illustrator` instructed to use the referenced element as image-to-image base (`reference_storage_ref`).
   - Step 3: `overlay_artist` for promotional badges, discount stickers, or typographic overlays.
   - Step 4: `caption_writer` for high-converting marketing copy and CTAs.
   NEVER collapse this down to a single step.
7b-video. **Video Deliverable:** Route 'dynamic' with:
   - Step 1: `script_writer` to plan the narrative script, scene flow, and pacing.
   - Step 2: `camera_director` instructed to animate the referenced element as `source_image_storage_ref`.
   - Step 3: `sound_designer` for voiceover, sound effects, and audio.
   - Step 4: `caption_writer` for video description and CTAs.
8. **Cross-Referencing & Memory:** Utilize retrieved memory and referenced elements to ground each specialist's instruction.
9. **Element Disambiguation:** When several candidate elements are present, decide which ones are targeted per rule 9.
10. **Parallel Groups:** Give independent parallel steps the same `parallel_group` number so they run concurrently (e.g. multiple platform image variations). Steps that depend on earlier outputs remain sequential (no parallel group).
11. **Sticky Focus:** Short follow-up tweaks to a previous single-specialist edit stay with that specialist.
12. **Multi-Platform Campaigns:** When multiple platforms or deliverables are requested (e.g. Twitter and LinkedIn), spawn concurrent `illustrator` steps for EACH platform using `parallel_group: 1`, with shared strategy upfront (`text_card_writer`) and cross-platform copy at the end (`caption_writer`).
13. **Multi-Image Multiplication:** When multiple product images are selected for a campaign, generate deliverables for each image.
14. **Granular, High-Context Instructions:** Each plan step MUST have an actionable, detailed instruction specifying aesthetics, lighting, copy, or parameters.
</rules>

<specialists>
{specialist_descriptions}
</specialists>

<output_format>
Return ONLY valid JSON — no prose, no markdown, no explanation, no <thought> blocks. Start your response directly with the opening curly brace.
{{
  "route": "Must be exactly one of: 'dynamic', 'direct_fix'",
  "target_specialist": "Required ONLY IF route is 'direct_fix'. MUST be a valid specialist name from the <specialists> section. Otherwise null.",
  "plan": [ // Required ONLY IF route is 'dynamic'. MUST have 4+ steps for any creative request.
    {{
      "specialist": "the exact name of the specialist",
      "instruction": "Detailed, actionable instruction with aesthetics, copy, and parameters — minimum 20 words per step",
      "parallel_group": "OPTIONAL integer per rule 10"
    }}
  ],
  "resolved_element_ids": "ONLY when multiple candidate elements are given — an array of ids, or omit/null."
}}
</output_format>
"""


def _fallback_grounding(message: str, brief_idea: str) -> str:
    """The actual request text to fold into each fallback instruction, so a degraded plan still
    reads as "for this specific ask" rather than the literal, un-filled-in phrase "the brief" —
    that phrase was a template placeholder, never meant to reach a user verbatim. Falls back to a
    generic phrase only when neither source has anything (e.g. an empty message)."""
    grounding = (brief_idea or message or "").strip()
    return grounding if grounding else "the user's request"


def _keyword_fallback_route(message: str, brief_idea: str = "") -> tuple[str, str | None, list[dict] | None]:
    msg = f"{message} {brief_idea}".lower()
    grounding = _fallback_grounding(message, brief_idea)
    if any(k in message.lower() for k in _FIX_KEYWORDS):
        if any(w in msg for w in ("overlay", "text", "badge", "price tag", "headline")):
            return "direct_fix", "overlay_artist", None
        return "direct_fix", "composition_artist", None

    if any(w in msg for w in ("video", "animate", "reel", "motion", "clip", "unboxing")):
        return "dynamic", None, [
            {"specialist": "script_writer", "instruction": f"Plan the narrative arc, scene breakdown, and visual pacing for: {grounding}", "parallel_group": None},
            {"specialist": "camera_director", "instruction": f"Direct, stage, and generate the product marketing video for: {grounding}", "parallel_group": None},
            {"specialist": "sound_designer", "instruction": f"Produce cinematic soundscape, pacing audio, and voiceover for: {grounding}", "parallel_group": None},
            {"specialist": "caption_writer", "instruction": f"Craft engaging social copy, hook, and video description for: {grounding}", "parallel_group": None},
        ]

    if any(w in msg for w in ("audio", "voiceover", "voice", "speech")):
        return "dynamic", None, [
            {"specialist": "script_writer", "instruction": f"Write an impactful, persuasive marketing voiceover script for: {grounding}", "parallel_group": None},
            {"specialist": "sound_designer", "instruction": f"Produce high quality voiceover and audio clip for: {grounding}", "parallel_group": None},
            {"specialist": "caption_writer", "instruction": f"Draft the accompanying caption and call to action for: {grounding}", "parallel_group": None},
        ]

    if any(w in msg for w in ("campaign", "package", "multi-platform", "social package", "social")):
        has_twitter = "twitter" in msg or "tweet" in msg
        has_instagram = "instagram" in msg or "insta" in msg

        steps = [
            {"specialist": "text_card_writer", "instruction": f"Draft the campaign positioning, core angles, and promotional value propositions for: {grounding}", "parallel_group": None},
        ]

        if has_twitter and has_instagram:
            steps.extend([
                {"specialist": "illustrator", "instruction": f"Generate a vibrant Instagram campaign visual for: {grounding}", "parallel_group": 1},
                {"specialist": "illustrator", "instruction": f"Generate a dynamic 16:9 Twitter/X banner visual for: {grounding}", "parallel_group": 1},
                {"specialist": "overlay_artist", "instruction": f"Apply bold promotional badge and sale text overlay to the campaign assets for: {grounding}", "parallel_group": None},
                {"specialist": "caption_writer", "instruction": f"Create engaging social copy, CTAs, and hashtags tailored for Instagram and Twitter for: {grounding}", "parallel_group": None},
            ])
        else:
            steps.extend([
                {"specialist": "illustrator", "instruction": f"Generate a bold, striking campaign hero visual for: {grounding}", "parallel_group": 1},
                {"specialist": "overlay_artist", "instruction": f"Apply bold promotional badge and sale text overlay to the campaign image for: {grounding}", "parallel_group": None},
                {"specialist": "caption_writer", "instruction": f"Create engaging marketing copy, CTAs, and announcement text for: {grounding}", "parallel_group": None},
            ])

        return "dynamic", None, steps

    # Universal default for any visual/marketing deliverable: complete multi-specialist craft plan
    return "dynamic", None, [
        {"specialist": "text_card_writer", "instruction": f"Formulate the creative hook, core messaging, and headline concept for: {grounding}", "parallel_group": None},
        {"specialist": "illustrator", "instruction": f"Generate a bold, striking marketing visual for: {grounding}", "parallel_group": 1},
        {"specialist": "overlay_artist", "instruction": f"Design and apply promotional badges, typographic overlay, and CTA elements for: {grounding}", "parallel_group": None},
        {"specialist": "caption_writer", "instruction": f"Write high-converting marketing copy, CTAs, and hashtags for: {grounding}", "parallel_group": None},
    ]

@traceable(name="orchestrator_node")
async def route(state: GraphState) -> GraphState:
    brief_stage_check = state.get("brief") or {}

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
    primary_req = brief.get("primary_user_request") or ""
    user_message = state.get("user_message") or ""
    referenced_elements = brief.get("referenced_elements_context", [])
    
    classification_context = [f"User's creative goal and instructions:\n{user_message or primary_req or brief_idea or '(none)'}"]
    if primary_req and primary_req not in user_message:
        classification_context.append(f"Overarching Campaign Request from User:\n{primary_req}")
    if brief_idea:
        classification_context.append(f"Merged brief so far:\n{brief_idea}")
        
    retrieved_memory = brief.get("_retrieved_memory")
    if retrieved_memory:
        classification_context.append(f"Relevant historical chat memory:\n{retrieved_memory}")
    
    # Fix 8 (2026-09-26) was considered here too, but deliberately NOT applied: this classifier
    # always runs on `ModelTier.TIER_1` (Groq's `gpt-oss-20b`, confirmed via `core/config.py`'s
    # own comment to be text-only — this codebase already has a
    # SEPARATE, dedicated `groq_vision_model` specifically because the tiered gpt-oss models can't
    # see images at all). Attaching real image content to this call risks a hard API error on the
    # single most load-bearing path in the app (every turn routes through this), with no safe way
    # to verify the failure mode without a real (paid) call — too risky to ship unverified here.
    # `_direct_fix_node`/`_dynamic_executor_node` keep their real image attachment (pre-existing,
    # already-shipped behavior for `_dynamic_executor_node`); this call site stays text-only.
    needs_disambiguation = bool(brief.get("_element_disambiguation_needed"))
    if referenced_elements:
        elements_desc = []
        for i, el in enumerate(referenced_elements, 1):
            kind = el.get("element_type", "unknown kind")
            desc = el.get("description", "(no description recorded)")
            elements_desc.append(f"Element {i} (id: {el.get('id')}, storage_ref: {el.get('storage_ref', 'none')}, Type: {kind}): {desc}")

        elements_str = "\n".join(elements_desc)
        if needs_disambiguation:
            # Fix 7 (2026-09-26): more than one real candidate — no explicit reference was given
            # by the user for this turn (see `session_service.py`), so which ONE (if any) applies
            # is a genuine judgment call, not a hardcoded recency default. Rule 9 covers how to
            # decide; `resolved_element_id` in the output carries the decision back.
            classification_context.append(
                f"Multiple existing elements are given as CANDIDATES to choose between (no "
                f"specific one was explicitly referenced this turn) — see rule 9:\n{elements_str}\n"
                f"Decide which ONE (if any) the user's most recent message is actually about."
            )
        else:
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
                f"and produce an unrelated new one instead of the edit being asked for.\n"
                f"HOWEVER, if the message asks for a NEW deliverable or a CAMPAIGN that uses this "
                f"element as reference material (a product ad, a poster, a thumbnail, a hero banner, a sale promo, or a campaign — see Rules 7b & 12): route 'dynamic'. "
                f"For EVERY deliverable, assemble a comprehensive multi-specialist plan: "
                f"text_card_writer for concept/hooks, illustrator instructed to use this element as image-to-image reference_storage_ref, "
                f"overlay_artist for promotional badges/discounts/CTA typography, and caption_writer for marketing copy/CTAs — "
                f"NEVER collapse ANY deliverable into a single-step plan! "
                f"If that NEW deliverable is a VIDEO instead (an unboxing clip, a reel, an animated ad) "
                f"built from this same element, route 'dynamic' with script_writer -> camera_director -> sound_designer -> caption_writer."
            )
    else:
        classification_context.append("An existing generated element is available to fix: no")

    # Sticky focus (2026-10-06, Ctruh Agent Engine cross-check): a signal the classifier never had
    # before — which specialist handled the immediately preceding turn. Purely informational, read
    # by rule 11 above; the model still decides, this never bypasses classification outright (per
    # this app's own standing preference: routing stays model-driven, fix the prompt, not a
    # deterministic override).
    last_specialist = brief.get("last_specialist")
    if last_specialist:
        classification_context.append(
            f"The specialist that handled the PREVIOUS turn in this session was: '{last_specialist}' "
            f"— see rule 11 (Sticky Focus)."
        )

    llm = get_llm_provider()
    
    import asyncio

    from ...providers.llm.laya_provider import LayaProvider
    
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
        # Real, live-found gap (2026-09-26): this single call decides EVERYTHING downstream — the
        # route, the target specialist, and the entire `dynamic` plan's task breakdown — yet it
        # ran on TIER_1 (`gpt-oss-20b`), the smallest configured model, no different from a
        # one-off text classification. Task decomposition quality is exactly the kind of judgment
        # this codebase already treats as worth a better model elsewhere (Fix 4/7's own
        # reasoning) — bumped to TIER_2 (`gpt-oss-120b`, already what TIER_3 uses by default too,
        # so this gives the SAME capability the actual generation specialists get, not a new tier
        # to configure).
        parsed = None
        last_route_err = None
        history_msgs = build_history_messages(brief, "\n\n".join(classification_context))
        parsed = None
        last_route_err = None

        for attempt in range(2):
            try:
                result = await llm.complete(
                    tier=ModelTier.TIER_2,
                    system=_SYSTEM_PROMPT.format(specialist_descriptions=describe_specialists()),
                    messages=history_msgs,
                    max_tokens=4096,
                )
                parsed = extract_json(result.text)
                if parsed and ("route" in parsed or "plan" in parsed):
                    break
                else:
                    if attempt == 0:
                        log.warning("orchestrator_llm_retry_format", extra={"_extra_raw": (result.text or "")[:200]})
                        history_msgs = [
                            *history_msgs,
                            {"role": "assistant", "content": result.text or ""},
                            {
                                "role": "user",
                                "content": (
                                    "Your previous response was not valid JSON or was missing 'route'. "
                                    "Return ONLY the required JSON object with 'route' and 'plan' or 'target_specialist'. "
                                    "No prose, reasoning, or <thought> blocks. Start directly with {."
                                ),
                            },
                        ]
            except Exception as exc:
                last_route_err = exc
                if attempt == 0:
                    log.warning("orchestrator_llm_retry", extra={"_extra_error": str(exc)})
                    history_msgs = [
                        *history_msgs,
                        {
                            "role": "user",
                            "content": (
                                "Your previous response raised an error or timed out. "
                                "Return ONLY the required JSON object with 'route' and 'plan' or 'target_specialist'. "
                                "No prose, reasoning, or markdown fences. Start directly with {."
                            ),
                        },
                    ]
        if parsed is None:
            raise ValueError(f"Failed to obtain valid JSON from routing model: {last_route_err}")
        chosen_route = str(parsed.get("route") or "")
        target_specialist = str(parsed.get("target_specialist") or "").strip() or None
        dynamic_plan = parsed.get("plan") or None

        # Fix 7 (2026-09-26): narrow the candidate set down to the model's real, reasoned decision
        # — downstream nodes (`_direct_fix_node`, `_dynamic_executor_node`) read
        # `brief["referenced_elements_context"]` directly, so this is what actually makes "try
        # again" regenerate the right element instead of always the most recent one in the
        # session. Only touched when disambiguation was genuinely needed (an explicit single
        # reference from the user is left completely alone, never second-guessed).
        if needs_disambiguation:
            resolved_element_ids = parsed.get("resolved_element_ids")
            matched = []
            if isinstance(resolved_element_ids, list):
                for rid in resolved_element_ids:
                    el = next((e for e in referenced_elements if e.get("id") == str(rid)), None)
                    if el:
                        matched.append(el)
            elif resolved_element_ids:
                # Fallback in case the model returns a single string instead of an array
                el = next((e for e in referenced_elements if e.get("id") == str(resolved_element_ids)), None)
                if el:
                    matched.append(el)
            
            if not matched:
                # The model couldn't disambiguate. Pause and ask the user directly!
                options = [
                    {"id": str(el.get("id")), "label": str(el.get("element_type", "Element")).title(), "description": str(el.get("description", ""))}
                    for el in referenced_elements
                ]
                state["paused_plan"] = {
                    "route": "element_disambiguation",
                    "original_message": user_message,
                    "referenced_elements": referenced_elements,
                }
                state["result"] = {
                    "message": "Which element from the canvas would you like to use?",
                    "options": options,
                    "allow_free_text": True,
                }
                state["route"] = "element_disambiguation"
                emit("lead_paused", lead="orchestrator", question=state["result"]["message"])
                return state

            brief["referenced_elements_context"] = matched
            brief.pop("_element_disambiguation_needed", None)
            state["brief"] = brief

        if chosen_route not in _VALID_ROUTES:
            raise ValueError(f"model returned an invalid route: {chosen_route!r}")
        if chosen_route == "direct_fix" and target_specialist not in SPECIALIST_REGISTRY:
            # A real, named failure mode rather than silently accepting a hallucinated specialist
            # name — fall through to dynamic rather than routing to something that doesn't exist.
            log.warning(
                "orchestrator_invalid_target_specialist",
                extra={"_extra_target": target_specialist},
            )
            chosen_route, target_specialist = "dynamic", None
            dynamic_plan = [{"specialist": "illustrator", "instruction": user_message}]

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

        # Persist for the NEXT turn's rule 11 (Sticky Focus) — only meaningful for direct_fix,
        # since that's the single-specialist "quick edit" shape a short follow-up like "make it
        # bigger" actually targets; a broader route (dynamic) has
        # no one specialist that obviously owns the next tweak, so reset rather than carry forward.
        brief["last_specialist"] = target_specialist if chosen_route == "direct_fix" else None
        brief["last_route"] = chosen_route
        state["brief"] = brief

        # Plan preview (2026-10-06, explicit user ask: show what will run BEFORE it runs, like
        # Luma). Persisted onto `state` (not just emitted) so `session_service.py` can save it onto
        # the turn's own row — same treatment `dynamic_plan` already gets.
        plan_preview = _build_plan_preview(chosen_route, target_specialist, dynamic_plan, user_message)
        state["plan_preview"] = plan_preview
        if plan_preview:
            emit("plan_proposed", route=chosen_route, plan=plan_preview)

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

        # Real, live-found gap closed (2026-10-07, explicit user ask: "after creating a plan there
        # should be hitl, be it campaign generation or any generation"): the plan preview above used
        # to be purely informational — shown, then auto-proceeded straight into execution with no
        # pause. Now it's a genuine gate for every real route (dynamic/direct_fix): pause here, before ANY specialist runs, and require an explicit
        # user approval. Reuses the exact `paused_plan`/`state["result"]` shape the existing
        # `SpecialistNeedsClarification` pause-resume machinery already uses (`state.py`), so
        # `session_service.py`'s existing persistence/resume dispatch (lines ~906-952) just needs
        # one more branch, no new mechanism. `route_condition` below sends this to END exactly like
        # the pre-existing `"approval_required"` Laya-fallback branch already does.
        if plan_preview:
            state["paused_plan"] = {
                "route": "plan_approval",
                "pending_route": chosen_route,
                "dynamic_plan": dynamic_plan,
                "target_specialist": target_specialist,
                "plan_preview": plan_preview,
                "original_message": user_message,
            }
            state["result"] = {
                "message": "Here's the plan — want me to go ahead?",
                "options": [
                    {"id": "approve", "label": "Approve", "description": "Run this plan as-is"},
                    {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"},
                ],
                "allow_free_text": True,
                "proposal": {"plan": plan_preview},
            }
            state["route"] = "plan_approval"
            emit("lead_paused", lead="orchestrator", question=state["result"]["message"])
            for scratch_key in ("_retrieved_memory",):
                if scratch_key in (state.get("brief") or {}):
                    state["brief"] = {k: v for k, v in state["brief"].items() if k != scratch_key}
            return state
    except (ProviderUnavailable, ValueError) as exc:
        # LLM gateways are down, or it returned something unparseable — no model available to
        # make Fix 7's real disambiguation judgment call, so this degraded path falls back to the
        # single most recent candidate (the old safe default) rather than leaving multiple
        # ambiguous candidates for a downstream node to sort out on its own.
        if needs_disambiguation and referenced_elements:
            brief["referenced_elements_context"] = [referenced_elements[-1]]
            brief.pop("_element_disambiguation_needed", None)
            state["brief"] = brief
        # Fallback handling when the primary LLM call encounters a provider or formatting error:
        fallback_route, fallback_target, fallback_plan = _keyword_fallback_route(user_message, brief_idea)
        combined_text = f"{user_message} {brief_idea}".lower()
        is_campaign_or_multi = (
            any(w in combined_text for w in ("campaign", "package", "multi-platform", "social package", "social", "twitter", "instagram", "video", "audio"))
            or (fallback_plan is not None and len(fallback_plan) > 1)
            or ("instagram" in combined_text and "twitter" in combined_text)
        )

        # For campaigns, multi-platform asks, videos, audio, or when no single target exists,
        # assemble a structured multi-agent plan for user approval rather than degrading to a single specialist.
        if is_campaign_or_multi or not fallback_target:
            log.warning(
                "orchestrator_routing_keyword_fallback",
                extra={"_extra_reason": str(exc), "_extra_is_campaign": is_campaign_or_multi},
            )
            plan_preview = _build_plan_preview(fallback_route, fallback_target, fallback_plan, user_message)
            state["paused_plan"] = {
                "route": "plan_approval",
                "pending_route": fallback_route,
                "dynamic_plan": fallback_plan,
                "target_specialist": fallback_target,
                "plan_preview": plan_preview,
                "original_message": user_message,
            }
            state["result"] = {
                "message": "I hit a snag putting together a fully custom plan, so here's a solid starting plan instead — feel free to tweak any step before approving.",
                "options": [
                    {"id": "approve", "label": "Approve", "description": "Run this plan as-is"},
                    {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"},
                ],
                "allow_free_text": True,
                "proposal": {"plan": plan_preview},
            }
            state["route"] = "plan_approval"
            state["plan_preview"] = plan_preview
            emit("plan_proposed", route="plan_approval", plan=plan_preview)
            emit("lead_paused", lead="orchestrator", question=state["result"]["message"])
            emit("route_decided", route="plan_approval", target_specialist=fallback_target, degraded=True)
            return state

        # If it is a narrow single-specialist tweak, try asking Laya for a single specialist recommendation
        options_list = list(SPECIALIST_REGISTRY.keys())
        try:
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
                "message": f"I wasn't fully sure how to route this, but I'd suggest **{laya_specialist}** — want me to go ahead?",
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
            plan_preview = _build_plan_preview(fallback_route, fallback_target, fallback_plan, user_message)
            state["paused_plan"] = {
                "route": "plan_approval",
                "pending_route": fallback_route,
                "dynamic_plan": fallback_plan,
                "target_specialist": fallback_target,
                "plan_preview": plan_preview,
                "original_message": user_message,
            }
            state["result"] = {
                "message": "I hit a snag putting together a fully custom plan, so here's a solid starting plan instead — feel free to tweak any step before approving.",
                "options": [
                    {"id": "approve", "label": "Approve", "description": "Run this plan as-is"},
                    {"id": "cancel", "label": "Cancel", "description": "Discard this idea and pivot"},
                ],
                "allow_free_text": True,
                "proposal": {"plan": plan_preview},
            }
            state["route"] = "plan_approval"
            state["plan_preview"] = plan_preview
            emit("plan_proposed", route="plan_approval", plan=plan_preview)
            emit("lead_paused", lead="orchestrator", question=state["result"]["message"])
            emit("route_decided", route="plan_approval", target_specialist=fallback_target, degraded=True)
            return state

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
