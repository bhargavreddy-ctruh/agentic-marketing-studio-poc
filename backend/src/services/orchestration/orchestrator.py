"""
The Orchestrator — exactly three routes, per Architecture.md section 1 (transcribed from the
source PDF's Section 7):

    1. Full still image  -> Visual Design Lead (+ Scene Lead if a background/scene is needed)
    2. Full video        -> Narrative Lead -> Scene Lead -> Motion Lead
    3. Single-element fix -> direct specialist call, bypassing its Lead

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
_FIX_KEYWORDS = ("fix", "just change", "just fix", "relight", "recolor")

_VALID_ROUTES = {"full_image", "full_video", "direct_fix"}

_SYSTEM_PROMPT = """You are the Orchestrator for a creative marketing studio. Given the user's
request, decide which of exactly three routes applies:

- "full_image": a brand new still image needs to be generated from scratch.
- "full_video": a brand new video needs to be generated from scratch.
- "direct_fix": a targeted fix to ONE existing element — no new generation from scratch, just one
  specialist adjusting something that already exists (e.g. "just fix the lighting", "relight this
  scene", "change the overlay text", "recolor the background").

If and only if the route is "direct_fix", also name the single specialist whose job matches the
request, from these, with the real tools each can call:
{specialist_descriptions}

Cost and latency discipline (this matters — pick the CHEAPEST specialist that can genuinely
satisfy the request): a wrong price/discount/text is a `text_overlay` +
`discount_claims_calculator` job (overlay_artist) — never worth routing to "full_image"/
"full_video" or a full-regeneration specialist just to fix a number. A color/lighting/composition
tweak is an `image_editor` job on the existing asset. Only choose "full_image"/"full_video" (a
brand new generation from scratch) when the request genuinely can't be satisfied by any targeted
specialist edit.

You will be given the user's most recent literal message AND a merged brief describing the
overall idea so far. Base your route decision on the MOST RECENT MESSAGE's actual intent — a
merged brief often rephrases a targeted fix request into plain descriptive language (e.g. "just
fix the lighting" can become "...with brighter lighting" once merged), which would wrongly look
like a fresh full-generation request if you only read the merged brief. "direct_fix" is only
possible when an existing element is mentioned as available — if none is, do not choose direct_fix
even if the message sounds like a tweak.

Return ONLY JSON:
{{
  "route": "full_image" | "full_video" | "direct_fix",
  "target_specialist": "one of the exact names above, ONLY if route is direct_fix, else empty string"
}}
"""


def _keyword_fallback_route(message: str) -> str:
    """The original Phase 0 heuristic — used only if the real LLM classification call itself
    fails, never as the default path."""
    if any(k in message for k in _FIX_KEYWORDS):
        return "direct_fix"
    if any(k in message for k in _VIDEO_KEYWORDS):
        return "full_video"
    return "full_image"


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
        emit("route_decided", route="full_video", target_specialist=None, resumed=True)
        return state

    # Both the raw message AND the merged brief are given to the classifier — a merged brief alone
    # loses "just fix X" framing once ideation paraphrases it into plain descriptive language
    # (Memory.md, Phase 3 conformance audit: a real bug found live this way — routing off the
    # merged brief alone sent an explicit "just fix the lighting" request through full
    # regeneration instead of direct_fix).
    brief = state.get("brief") or {}
    brief_idea = brief.get("idea") or ""
    user_message = state.get("user_message") or ""
    latest_ref = brief.get("latest_element_storage_ref")

    classification_context = [f"User's most recent literal message:\n{user_message or '(none)'}"]
    if brief_idea:
        classification_context.append(f"Merged brief so far:\n{brief_idea}")
    classification_context.append(
        f"An existing generated element is available to fix: {'yes' if latest_ref else 'no'}"
    )

    llm = get_llm_provider()
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_SYSTEM_PROMPT.format(specialist_descriptions=describe_specialists()),
            messages=[{"role": "user", "content": "\n\n".join(classification_context)}],
            max_tokens=1536,
            # Real, live-found reason (2026-09-21, same category as ideation_service.py's own
            # comment): a side-by-side test on a brand-new session (no existing element) had the
            # self-hosted TIER_1 model route to "direct_fix" anyway — directly disobeying this
            # prompt's own explicit "if none is [available], do not choose direct_fix" rule —
            # while Groq correctly returned "full_image". A wrong route here wastes a whole
            # generation attempt, so this classification skips local-first routing.
            prefer_local=False,
            # Real live "thinking" text, per the user's explicit ask (2026-09-21).
            on_delta=lambda delta: emit("llm_delta", node="orchestrator", text=delta),
        )
        parsed = extract_json(result.text)
        chosen_route = str(parsed.get("route") or "")
        target_specialist = str(parsed.get("target_specialist") or "").strip() or None

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

        state["route"] = chosen_route
        state["target_specialist"] = target_specialist
        log.info(
            "orchestrator_routed",
            extra={"_extra_route": chosen_route, "_extra_target": target_specialist, "_extra_method": "llm"},
        )
        emit("route_decided", route=chosen_route, target_specialist=target_specialist)
    except (ProviderUnavailable, ValueError) as exc:
        # Both LLM gateways are down, or it returned something unparseable — degrade to the old
        # heuristic rather than crashing the turn, but log it clearly as a degraded path.
        fallback = _keyword_fallback_route((user_message or brief_idea).lower())
        state["route"] = fallback
        state["target_specialist"] = None
        log.warning(
            "orchestrator_routing_fallback",
            extra={"_extra_route": fallback, "_extra_reason": str(exc)},
        )
        emit("route_decided", route=fallback, target_specialist=None, degraded=True)

    return state


def route_condition(state: GraphState) -> str:
    """The conditional-edge selector LangGraph calls after `route` runs."""
    return state.get("route") or "full_image"
