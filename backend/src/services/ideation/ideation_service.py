"""
The Ideation node — Architecture.md section 1d, now real (Phase 1 replaces the Phase 0
placeholder). Uses Tier 1 (small/fast, cheap) since deciding "is this brief specific enough" and
proposing a couple of creative directions doesn't need frontier-model judgment — that's reserved
for the Illustrator (Rules.md's model-tiering principle in practice).
"""
from __future__ import annotations

from ...core.events import emit
from ...core.exceptions import SpecialistFailed
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..orchestration.state import GraphState

log = get_logger(__name__)

_SYSTEM_PROMPT = """You are the ideation partner for a product marketing creative studio.

Given a running brief (what the user has told you so far) and their latest message, decide:
1. Is there enough here to start generating a still image (a clear subject/idea, even if brand and
   product details are still missing — those can be added later)?
2. If not, propose 2 concrete, pickable creative directions (not open questions) plus always allow
   free text instead.

Never ask more than one thing at a time. Prefer proposing options over asking an open question.

Return ONLY JSON:
{
  "ready": true or false,
  "merged_brief": {"idea": "a clear, one-paragraph synthesis of the brief so far"},
  "message": "a short line stating what's still needed, only used when ready is false",
  "options": [{"id": "short_id", "label": "Bold label", "description": "one-line rationale"}]
}
"""


@traceable(name="ideation_node")
async def run_ideation(state: GraphState) -> GraphState:
    brief = state.get("brief") or {}
    user_message = state.get("user_message") or ""

    # Resuming a paused "approve" mode video pipeline (Memory.md, Phase 4) — the user's message is
    # an approval/revision reply to an already-staged proposal, not new material to ideate on.
    # Skip straight through to the Orchestrator, which itself also short-circuits straight back to
    # the video pipeline (real, explicit state tracking is more reliable here than asking an LLM
    # to infer "we're mid-approval-flow" from a bare "approve" message with no other context).
    if brief.get("video_stage") in ("narrative_pending", "scene_pending"):
        state["route"] = None
        state["result"] = None
        return state

    llm = get_llm_provider()
    context = f"Running brief so far:\n{brief}\n\nLatest message from the user:\n{user_message}"

    emit("ideation_started")
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
            # 512 was too tight in practice (Memory.md, Phase 1): several free-tier models spend
            # real tokens on internal reasoning before or interleaved with the visible JSON
            # content, and got cut off mid-response at the lower budget.
            max_tokens=1536,
            # Real, live-found reason (2026-09-21): the self-hosted TIER_1 model's judgment on
            # "is this brief ready" is measurably worse than Groq's — a side-by-side test on the
            # exact same input ("A red Ferrari") had the local model return `ready: false` and
            # silently drop "red Ferrari" from its own brief synthesis. Ideation gates the whole
            # conversation and any detail it drops never comes back, so it skips local-first
            # routing entirely rather than risk that — unlike the tool-calling Tier 1 specialists,
            # which tested fine locally and keep the default.
            prefer_local=False,
        )
        parsed = extract_json(result.text)
    except Exception as exc:  # provider or parse failure — fail this turn clearly, don't crash the graph
        log.error("ideation_failed", extra={"_extra_error": str(exc)})
        raise SpecialistFailed("ideation", str(exc)) from exc

    # Defensive coercion, not trust — a free-tier model asked for {"idea": "..."} has, in real
    # testing (Memory.md, Phase 1), returned a bare string instead. Ported pattern from the
    # existing agentic_flow codebase's run_product_intelligence: never assume the model's JSON
    # matches the requested shape exactly, coerce it into something usable instead of crashing.
    raw_merged = parsed.get("merged_brief")
    if isinstance(raw_merged, dict):
        merged_brief = raw_merged
    elif isinstance(raw_merged, str) and raw_merged.strip():
        merged_brief = {"idea": raw_merged.strip()}
    else:
        merged_brief = {}
    state["brief"] = {**brief, **merged_brief}

    raw_options = parsed.get("options")
    options = [
        opt for opt in (raw_options if isinstance(raw_options, list) else [])
        if isinstance(opt, dict) and opt.get("id") and opt.get("label")
    ]

    if parsed.get("ready") and merged_brief.get("idea"):
        state["route"] = None  # let the Orchestrator decide the route from the merged brief
        state["result"] = None
    else:
        state["result"] = {
            "message": str(parsed.get("message") or "Tell me more about what you have in mind."),
            "options": options,
            "allow_free_text": True,
        }

    log.info("ideation_turn", extra={"_extra_session_id": state.get("session_id"), "_extra_ready": parsed.get("ready")})
    emit("ideation_completed", ready=bool(parsed.get("ready")))
    return state
