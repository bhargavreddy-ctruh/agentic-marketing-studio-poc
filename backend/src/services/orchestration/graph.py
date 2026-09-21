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

from langgraph.graph import END, StateGraph

from ...core.approval import is_approval
from ...core.events import emit
from ...core.exceptions import SpecialistFailed
from ...core.middleware.logging import get_logger
from ...providers.observability.langsmith import traceable
from ..ideation.ideation_service import run_ideation
from ..leads.base import NarrativePlan, ScenePlan
from ..leads.motion_lead import run_motion_lead
from ..leads.narrative_lead import run_narrative_lead
from ..leads.scene_lead import run_scene_lead
from ..leads.visual_design_lead import run_visual_design_lead
from ..specialists.runner import run_specialist_agentic
from .orchestrator import route, route_condition
from .state import GraphState

log = get_logger(__name__)


@traceable(name="visual_design_lead_node")
async def _visual_design_lead_node(state: GraphState) -> GraphState:
    emit("lead_started", lead="visual_design_lead")
    try:
        result = await run_visual_design_lead(brief=state.get("brief") or {})
        # LeadResult -> dict only here, at the LangGraph-mandated TypedDict boundary (GraphState) —
        # the one accepted exception to "no dict crossing a layer boundary" (Rules.md section 2).
        state["result"] = result.to_dict()
        emit("lead_completed", lead="visual_design_lead")
    except SpecialistFailed as exc:
        log.error("visual_design_lead_failed", extra={"_extra_error": exc.message})
        state["error"] = exc.message
        state["result"] = {"message": f"Could not produce an image: {exc.message}"}
        emit("lead_failed", lead="visual_design_lead", reason=exc.message)
    return state


def _pending_approval_result(*, stage: str, message: str, proposal: dict) -> dict:
    """The same message+options+free-text shape ideation already uses — a real approval prompt
    reuses that existing, already-tested pattern rather than inventing a new response shape."""
    return {
        "message": message,
        "options": [
            {"id": "approve", "label": "Approve", "description": "Proceed to the next stage"},
            {"id": "revise", "label": "Request changes", "description": "Describe what to change"},
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


@traceable(name="full_video_pipeline_node")
async def _motion_lead_node(state: GraphState) -> GraphState:
    """
    The real full-video pipeline: Narrative Lead -> Scene Lead -> Motion Lead (Architecture.md
    section 2.1).

    In "auto" mode (default, unchanged from before): runs straight through with no pauses, exactly
    as it always has.

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
    brief = state.get("brief") or {}
    approval_mode = brief.get("approval_mode", "auto")
    stage = brief.get("video_stage")  # None | "narrative_pending" | "scene_pending" | "motion_pending" | "done"
    user_message = state.get("user_message") or ""

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
            else:
                emit("lead_started", lead="narrative_lead", revision=True)
                revised_brief = {
                    **brief,
                    "idea": f"{brief.get('idea', '')}\n\nRevision feedback on the proposed shots: {user_message}",
                }
                narrative = await run_narrative_lead(brief=revised_brief)
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
            narrative = await run_narrative_lead(brief=brief)
            emit("lead_completed", lead="narrative_lead")
            if approval_mode == "approve":
                return _stage_narrative_for_approval(narrative, revised=False)

        # === Stage 2: get a ScenePlan, either fresh, approved-from-staged, or revised ===
        if stage == "scene_pending":
            if is_approval(user_message):
                scene = ScenePlan.from_dict(brief["scene_plan"])
            else:
                emit("lead_started", lead="scene_lead", revision=True)
                scene = await run_scene_lead(
                    shot_description=f"{narrative.shots[0]}\n\nRevision feedback on the proposed scene: {user_message}"
                )
                emit("lead_completed", lead="scene_lead", revision=True)
                return _stage_scene_for_approval(scene, revised=True)
        elif stage == "motion_pending":
            # Scene was already approved in an earlier turn too — reload it, never regenerate it.
            scene = ScenePlan.from_dict(brief["scene_plan"])
        else:
            emit("lead_started", lead="scene_lead")
            scene = await run_scene_lead(shot_description=narrative.shots[0])
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
                brief["video_stage"] = None
                brief.pop("narrative_plan", None)
                brief.pop("scene_plan", None)
                state["brief"] = brief
                state["result"] = {
                    "message": "Video generation cancelled — nothing was rendered and nothing was "
                    "spent. Send a new idea whenever you're ready."
                }
                emit("lead_failed", lead="motion_lead", reason="cancelled_before_spend")
                return state
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
        state["error"] = exc.message
        state["result"] = {"message": f"Could not produce a video: {exc.message}"}
        emit("lead_failed", lead="full_video_pipeline", reason=exc.message)
    return state


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

    context_parts = [f"User request:\n{state.get('user_message', '')}"]
    if brief.get("idea"):
        context_parts.append(f"Campaign idea so far:\n{brief['idea']}")
    latest_ref = brief.get("latest_element_storage_ref")
    if latest_ref:
        # The exact original generation prompt, not just the general campaign idea — real
        # grounding a text-only specialist needs, since it has no memory of its own prior run and
        # can't see the storage_ref's actual pixels (Memory.md, Phase 4: the same real bug found
        # in regenerate/comment resolution — without this, a fix could invent an unrelated image).
        description = brief.get("latest_element_description")
        context_parts.append(
            f"The most recent generated element (storage_ref: {latest_ref}, "
            f"type: {brief.get('latest_element_type', 'unknown')}) depicts:\n"
            f"{description or '(no original prompt was recorded for this element)'}\n"
            f"Act on THIS existing asset, keeping everything the same except what the user's "
            f"request asks to change — do not generate an unrelated new one."
        )

    emit("lead_started", lead="direct_fix", target_specialist=target)
    try:
        step = await run_specialist_agentic(target, context="\n\n".join(context_parts))
    except SpecialistFailed as exc:
        log.error("direct_fix_failed", extra={"_extra_specialist": target, "_extra_error": exc.message})
        state["error"] = exc.message
        state["result"] = {"message": f"Could not apply the fix via {target}: {exc.message}"}
        emit("lead_failed", lead="direct_fix", reason=exc.message)
        return state

    produced_ref, produced_tool = None, None
    for call in reversed(step.tool_calls):
        if call.ok and call.data.get("storage_ref"):
            produced_ref, produced_tool = call.data["storage_ref"], call.tool_name
            break

    if not produced_ref:
        # The specialist ran and reasoned, but genuinely made no tool call that produced a new
        # asset — an honest non-result, not an error (Rules.md section 2: no fabricated success).
        state["result"] = {
            "message": f"{target} considered the request but did not produce a new asset.",
            "specialist_notes": step.data,
        }
        emit("lead_completed", lead="direct_fix", no_op=True)
        return state

    result: dict = {
        "storage_ref": produced_ref,
        "produced_by_specialist": target,
        "element_type": brief.get("latest_element_type", "image"),
        "metadata": {"direct_fix": True, "tool_used": produced_tool, **step.data},
    }
    if brief.get("latest_element_id"):
        result["update_existing_element_id"] = brief["latest_element_id"]
    state["result"] = result
    emit("lead_completed", lead="direct_fix")
    return state


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("ideation", run_ideation)
    graph.add_node("orchestrator", route)
    graph.add_node("visual_design_lead", _visual_design_lead_node)
    graph.add_node("motion_lead_pipeline", _motion_lead_node)
    graph.add_node("direct_fix", _direct_fix_node)

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
            "full_image": "visual_design_lead",
            "full_video": "motion_lead_pipeline",
            "direct_fix": "direct_fix",
        },
    )
    graph.add_edge("visual_design_lead", END)
    graph.add_edge("motion_lead_pipeline", END)
    graph.add_edge("direct_fix", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
        log.info("graph_compiled")
    return _compiled_graph
