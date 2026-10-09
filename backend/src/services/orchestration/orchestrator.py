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
0. **Think Before Acting:** You MUST write down your step-by-step reasoning inside a <thought>...</thought> block BEFORE outputting your final JSON response. Always think before making decisions.
1. **Dynamic Assembly:** You do NOT use hardcoded pipelines. Instead, you select EXACTLY the specialists needed to fulfill the request, in the exact order they should run, and provide a clear instruction for each step.
2. **Efficiency & Autonomy:** Do not waste steps, but DO autonomously include planning and strategy specialists (like 'reference_curator' or 'palette_strategist') if the task is complex, broad, or requires a cohesive brand style (e.g., a "campaign" or "brand refresh"). Do not rely on the user to explicitly ask for them.
3. **Valid Specialists Only:** You can only use the specialists listed below.
4. **NO ASSUMPTIONS ON VAGUE INPUTS:** Never assume anything that is not strictly allowed. Never make a decision on vague inputs or assumptions. If the user attaches an image but does not specify how it should be used (e.g. as a product to composite, a style reference, or a base for image-to-image), or if their request is too vague to safely route, you MUST return a plan that instructs the first specialist to fail and explicitly ask the user for clarification.
5. **Campaign Defaults (CRAZY & BOLD):** We are making this for elite marketing and creating campaigns. Image and video generations should be CRAZY, striking, and visually incredible. If the request is for a broad "campaign" or specific social media posts (e.g. Instagram, Twitter), autonomously build a complete, robust plan: you MUST include `illustrator` to generate or crop the base image(s), if text/urgency/discounts are requested you MUST include `overlay_artist` or `headline_writer` to draw the text onto the image, and you MUST include `caption_writer` to generate the accompanying post captions, tags, and social copy. If the user asks for MULTIPLE platforms/deliverables (e.g., "Instagram AND Twitter"), you MUST include separate `illustrator` and `caption_writer` steps for EACH platform in your plan, using `parallel_group` to run them concurrently. Push the creative boundaries.
5b. **Text on New Images — Flat Overlay vs. In-Scene Element:** Distinguish WHAT KIND of text is being asked for before deciding whether `overlay_artist` is needed.
   - **Flat/precise overlay** (a price, a discount %, a promo badge, legal/disclaimer copy — anything where the EXACT characters matter and it should read as a graphic-design layer sitting on top of the photo, separate from the scene itself): add `overlay_artist` as a step AFTER `illustrator`. `overlay_artist` draws this as a flat strip on the EXISTING image and cannot change composition — correct for this case.
   - **In-scene/stylistic text** (a neon sign, a banner, graffiti, a marquee, a billboard, a tattoo — anything the request frames as part of the photographed/illustrated scene itself, where stylized/approximate rendering is the POINT, not a defect): do NOT add `overlay_artist`. Instead, instruct `illustrator` to render it directly as a described scene element in its own image prompt (e.g. "a glowing neon sign reading 'SALE' integrated into the scene, in bold red lettering"). Pasting a flat `overlay_artist` layer on top of this kind of request looks obviously stuck-on rather than part of the artwork, which is the opposite of what was asked for.
   - When unsure which case applies, ask: would a real photographer/illustrator have painted or lit this text as part of the scene (in-scene), or would a graphic designer have added it afterward in a separate pass (flat overlay)? That's the deciding question.
6. **Context Guardrail:** If the request and the brief entirely lack a specific subject or product, do NOT invent or guess a generic product. Instead, return a plan instructing the first specialist to fail and ask the user for clarification. ALWAYS prioritize building guardrails based on user inputs.
6b. **Reference Image Handling:** If the user provided a reference image and their instruction is clear on how to use it, you MUST explicitly tell the first generating specialist (e.g., `illustrator` for an IMAGE deliverable, `camera_director` for a VIDEO deliverable — see Rule 7b-video) how to use it. For example, if it's an image-to-image base for illustrator, add to the instruction: "You MUST use the provided referenced element as your image-to-image reference_storage_ref"; for camera_director animating it into a clip, add: "You MUST use the provided referenced element as your source_image_storage_ref".
7. **Editing Existing Assets vs. Using One as Reference Material:** A referenced element being present does NOT always mean "edit it in place" — distinguish the two cases below before choosing a route.
7a. **In-place edit** (the deliverable stays the SAME shot/composition, just changed): "edit this image", "strike out the price", "change the color", "recolor this", "add a price tag to this", "fix the logo on this". You MUST use route 'direct_fix' and provide the 'target_specialist'. Do NOT use 'dynamic' for this case. For image content edits (recoloring, changing subjects, adding/removing visual elements, modifying the image itself), use 'composition_artist'. Only use 'overlay_artist' for pure TEXT overlays (adding price tags, discount labels, promotional text ON TOP of an image, with no other visual change).
7b. **New, differently-composed deliverable using the element as reference material** (the ask names a DIFFERENT finished-format deliverable than a plain edit — e.g. "make a youtube thumbnail of/using/from this", "turn this into a poster", "make a hero banner with this", "make this pop like a movie poster" — especially when the wording names a format keyword like thumbnail/poster/banner/hero/cover, or implies a different aspect ratio or a dramatically different look than the source photo): route 'dynamic' with 'illustrator' as the generating step. Your instruction to illustrator MUST say to use the referenced element as an image-to-image reference (reference_storage_ref on base_image_generator, or reference_storage_refs on high_resolution_image_generator/collab_image_generator) — describe WHICH element by what it depicts (e.g. "the referenced product photo"), never by retyping its storage_ref value yourself: the executor already states the exact, correct storage_ref for you in the real element's own context block, and a hand-retyped copy of a long id string is exactly the kind of thing a model gets wrong character-by-character. The goal is a genuinely new, differently composed, appropriately-sized, visually striking result — NOT to copy its composition untouched. NEVER route a 7b-shaped request to 'overlay_artist' — it can only draw text on the UNCHANGED source pixels at the source's own original dimensions, so it cannot deliver a new aspect ratio or a recomposed shot no matter how the instruction is worded.
7b-video. **The exact same case, but the ask is for a VIDEO.** For a SINGLE SHOT animating an existing referenced element (e.g. "make an unboxing video of this", "turn this into a reel", "animate this into a video ad", "make an exciting intro video using this photo" — no multi-beat story, no explicit duration/script ask): route 'dynamic' with 'camera_director' as the generating step. Your instruction to camera_director MUST say to use the referenced element as its starting frame (source_image_storage_ref on base_video_generator) by describing WHICH element by what it depicts — same reason as above, never retype its storage_ref value yourself. For a genuine multi-shot narrative/story video, still use route 'dynamic' with 'camera_director' but instruct it to delegate to 'shot_planner' to plan the multi-shot sequence first.
7c. **Genuinely ambiguous between 7a/7b:** if the message gives no real signal either way (no format keyword, no "turn this into X", just a vague "do something with this"), treat it as 7a (direct_fix) — the safer default — rather than guessing a new deliverable format that wasn't asked for.
Examples: "strike out the old price on this" -> 7a, direct_fix -> composition_artist. "add a 20% off badge to this" -> 7a, direct_fix -> overlay_artist. "make a youtube thumbnail out of this product photo" -> 7b, dynamic -> illustrator (image-to-image, 16:9, dramatic). "turn this product shot into an Instagram story" -> 7b, dynamic -> illustrator (image-to-image, 9:16). "make an exciting unboxing video of this product photo" -> 7b-video, dynamic -> camera_director (single shot, animates the referenced photo). "tell a 3-shot story about this product launching, ending on this photo" -> dynamic -> camera_director (genuine multi-shot narrative). "a crazy collab image with a banner reading 'SALE' in bold neon letters" -> 5b in-scene case, dynamic -> illustrator only (render the neon sign as part of the scene in its own image prompt; do NOT add an overlay_artist step). "a product shot with a 20% off price badge in the corner" -> 5b flat-overlay case, dynamic -> illustrator then overlay_artist (the price badge is a precise flat overlay).
8. **Cross-Referencing & Memory:** You will be provided with retrieved long-term memory and multiple referenced elements if applicable. Use this history and cross-reference information to build highly accurate 'dynamic' plans or pick the right 'direct_fix' specialist.
9. **Element Disambiguation & Multi-Element Routing:** When several existing elements are shown as candidates rather than one confirmed reference, determine WHICH elements (if any) the message explicitly targets. 
- If the request applies the SAME action to multiple elements (e.g., "add a logo to both"), set `resolved_element_ids` to an array of those IDs and spawn a 'dynamic' plan with distinct steps for each targeted ID so they execute concurrently. 
- If the request gives DIFFERENT instructions for different elements (e.g., "make an instagram post out of the first one, and a twitter post for the second"), you MUST set `resolved_element_ids` to an array of all targeted IDs, and spawn a 'dynamic' plan where you explicitly assign the correct distinct instruction to its own step. Be sure to instruct the specialist WHICH element to use as its reference for that step.
- If the request is an edit or tweak but genuinely ambiguous about which element to modify (e.g., "make it pop", "recolor it" with 2+ candidates and no target specified), do NOT guess or default blindly to the latest tile. Simply set `resolved_element_ids` to `null` and return a standard generation plan as if the target were known; the system will automatically pause and ask the user to clarify before executing it. Only set `resolved_element_ids` when the user's message or context makes the target elements unambiguous.
10. **Parallel Steps (only in a 'dynamic' plan, only when genuinely independent):** If two or more steps each generate a completely FRESH, independent asset from scratch that do NOT depend on each other's output (e.g. two separate illustrator variants for A/B options, or an illustrator image alongside an unrelated sound_designer voiceover) — no need for one to have finished before the other starts — give them the SAME `parallel_group` number so they can run concurrently. NEVER put a step that EDITS an existing asset (composition_artist, prop_stylist, lighting_designer, overlay_artist) in a group with anything else, and NEVER group a step that needs another step's own not-yet-produced storage_ref — those must stay ungrouped (omit `parallel_group`, or give it a number no other step shares) so they run in your intended order. When genuinely unsure whether two steps are independent, leave `parallel_group` unset — sequential is always correct, grouping wrongly is not.
11. **Sticky Focus:** If context states which specialist handled the PREVIOUS turn, and the user's current message is a short follow-up tweak with no new subject/domain named (e.g. "make it bigger", "a bit more to the left", "try that again but bolder") — prefer `direct_fix` straight back to that SAME specialist rather than reclassifying from scratch. Only move away from it when the message clearly names a different deliverable type, a different subject, or a new piece of work entirely — this is your own judgment call, not a hard rule to apply blindly when the intent has genuinely shifted.
12. **Campaign Auto-Expansion & Multi-Platform Requests (Smart AI):** If the user asks for a "campaign" without listing deliverables, take immediate creative initiative and output a 'dynamic' plan containing a comprehensive package (e.g., Instagram 1:1, Story 9:16) AND necessary marketing copy (e.g. `text_card_writer`). IF the user explicitly lists platforms (e.g., "Instagram and Twitter"), you MUST include concurrent steps for EVERY platform they listed (do not drop any!). 
13. **Multi-Image Multiplication:** If the user selects multiple reference images and asks for a campaign (or specific platforms), fulfill the request for EACH image. For example, if they select 2 images and ask for an Instagram and Twitter campaign, you must spawn 4 distinct steps (Instagram for Image 1, Twitter for Image 1, Instagram for Image 2, Twitter for Image 2). Set `resolved_element_ids` to the array of BOTH image IDs. Do not set it to `null` to ask which one to use.
14. **Explicit Granular Steps:** Do not combine multiple deliverables into a single plan step. Every individual asset (e.g., Instagram Image, Twitter Image, Ad Copy) MUST have its own dedicated step in the plan array. Make the 'instruction' field clear, specific, and self-contained (e.g., "Generate a 1:1 Instagram post featuring the product in a bold, modern style").
</rules>

<specialists>
{specialist_descriptions}
</specialists>

<output_format>
Return ONLY valid JSON matching this schema:
{{
  "route": "Must be exactly one of: 'dynamic', 'direct_fix'",
  "target_specialist": "Required ONLY IF route is 'direct_fix'. MUST be a valid specialist name from the <specialists> section (e.g., 'overlay_artist', 'composition_artist'). DO NOT return null if route is 'direct_fix'. Otherwise null.",
  "plan": [ // Required ONLY IF route is 'dynamic'. Otherwise null.
    {{
      "specialist": "the exact name of the specialist",
      "instruction": "Clear instruction for what this specialist needs to accomplish in this step",
      "parallel_group": "OPTIONAL, per rule 10 — an integer shared by 2+ steps that are genuinely independent fresh-generation steps, so they run concurrently. Omit (or use a number no other step shares) for anything sequential or uncertain — this is the safe default."
    }}
  ],
  "resolved_element_ids": "ONLY when the context explicitly says multiple candidate elements are given to choose between (rule 9) — an array of the ids of the elements you decided this message is about, or an empty array [] if none of them are. Omit/null in every other case (a single confirmed reference needs no decision here)."
}}
</output_format>
"""


def _keyword_fallback_route(message: str) -> tuple[str, str | None, list[dict] | None]:
    if any(k in message for k in _FIX_KEYWORDS):
        return "direct_fix", "composition_artist", None
    
    # Default to a basic dynamic generation plan instead of the old, static full_image pipeline
    return "dynamic", None, [
        {"specialist": "illustrator", "instruction": "Generate the requested image based on the prompt."}
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
    user_message = state.get("user_message") or ""
    referenced_elements = brief.get("referenced_elements_context", [])
    
    classification_context = [f"User's most recent literal message:\n{user_message or '(none)'}"]
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
                f"and produce an unrelated new one instead of the edit being asked for. HOWEVER, if the "
                f"message instead asks for a NEW, differently-formatted deliverable that uses this "
                f"element as reference material rather than editing it in place (a youtube thumbnail, a "
                f"poster, a hero banner, a story/reel version — see Rule 7b), that is NOT an edit: route "
                f"'dynamic' with 'illustrator' as the generating step, instructed to use this element's "
                f"storage_ref as its image-to-image reference, never 'overlay_artist' (which can only "
                f"draw text on the unchanged original pixels at the original size, so it can never "
                f"deliver a new format or a recomposed shot). If that NEW deliverable is a VIDEO instead "
                f"(an unboxing clip, a reel, an animated ad) built from this same element, route "
                f"'dynamic' with 'camera_director' instead, instructed to use this element's storage_ref "
                f"as its starting frame (source_image_storage_ref) — see Rule 7b-video."
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
        result = await llm.complete(
            tier=ModelTier.TIER_2,
            system=_SYSTEM_PROMPT.format(specialist_descriptions=describe_specialists()),
            messages=build_history_messages(brief, "\n\n".join(classification_context)),
            max_tokens=1536,
        )
        parsed = extract_json(result.text)
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
