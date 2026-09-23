"""
Synchronous per-agent steps for Creative Studio node workflows.

Each function runs one campaign agent and returns a JSON-serializable result.
Existing SSE /api/run and /api/video-run are untouched.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from .agents.crew import (
    run_art_director_agent,
    run_concept_agent,
    run_guardian_agent,
    run_guardrail_agent,
    run_marketing_agent,
    run_product_agent,
    run_qa_agent,
    run_storyboard_agent,
    run_video_director,
    run_video_director_direct,
)
from .hitl_agents import run_critique, run_guardrail_agent, run_product_intelligence
from .config import DEFAULT_ASPECT_RATIO, DEFAULT_ASSET_COUNT, MAX_ASSET_COUNT, MAX_QA_RETRIES
from .llm_provider import CampaignLLMError, require_llm_key
from .logger import get_logger
from ..agent_core.brand_context import BrandDetails, ProjectDetails
from ..agent_core.gates import (
    GATE_CONCEPT,
    GATE_COPY,
    GATE_POST_IMAGE,
    GATE_STORYBOARD,
)
from ..agent_core.guardian import (
    TARGET_CONCEPT,
    TARGET_COPY,
    TARGET_IMAGE,
    TARGET_LOGO,
    TARGET_STORYBOARD,
    GuardianVerdict,
)
from ..agent_core.guardrails import coerce_set as coerce_guardrails
from ..agent_core.revision import coerce_revision, render_text_revision
from .agent_runtime import EmitFn
from .tools import CampaignRunContext
from .video_tools import VideoRunContext

log = get_logger(__name__)


async def _noop_emit(_event: dict[str, Any]) -> None:
    return None


def _clamp_assets(n: int | None) -> int:
    return max(1, min(int(n or DEFAULT_ASSET_COUNT), MAX_ASSET_COUNT))


# "scene_3", "scene-3", "Scene 3", "3" — all the same request.
_SCENE_ALIAS = re.compile(r"^(?:scene_)?(\d+)$")


def _norm_scene(value: Any) -> str:
    """A scene id reduced to what two spellings of the same name share."""
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _resolve_scene_ids(
    requested: list[str] | None, storyboard: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """
    Requested scene ids, mapped onto the ids this storyboard actually carries.

    A caller naming scenes has to name them before they exist. The planner
    writes params.sceneIds when it splits one storyboard across two branches,
    and it does that on an earlier call than the one that writes the ids.
    Reported: a graph asking for ['scene_1'] against a storyboard whose scenes
    were called scene-1 and scene-2, which failed the node outright.

    submit_storyboard assigns scene_1..scene_N by position now, so the two
    sides agree by construction. This exists for everything that predates that
    or arrives from outside it — an imported flow, a saved graph, an id typed
    by hand. Three ways to match, in descending confidence: the id exactly,
    the id ignoring case and separators, then position for anything shaped
    like "scene 3" or "3". Exact and normalized both beat position, so a
    storyboard that really does number its scenes out of order resolves to the
    scene named rather than the one in that slot.

    Resolved ids come back in the order they were REQUESTED, not storyboard
    order: for a film that list is the playback order and reordering it would
    silently recut the film. A caller that wants storyboard order (the image
    step) filters the storyboard by membership and gets it anyway.
    """
    real = [str(s.get("id") or "").strip() for s in storyboard if isinstance(s, dict)]
    real = [r for r in real if r]
    by_exact = {r: r for r in real}
    by_norm = {_norm_scene(r): r for r in real}

    resolved: list[str] = []
    unmatched: list[str] = []
    for raw in requested or []:
        asked = str(raw).strip()
        if not asked:
            continue
        hit = by_exact.get(asked) or by_norm.get(_norm_scene(asked))
        if hit is None:
            match = _SCENE_ALIAS.match(_norm_scene(asked))
            if match:
                index = int(match.group(1))
                if 1 <= index <= len(real):
                    hit = real[index - 1]
        if hit is None:
            unmatched.append(asked)
        elif hit not in resolved:
            resolved.append(hit)
    return resolved, unmatched


def _make_ctx(
    *,
    idea: str,
    product_images_b64: list[dict[str, str]],
    product_image_urls: list[str] | None = None,
    brand_kit: dict[str, Any] | None = None,
    asset_count: int | None = None,
    aspect_ratio: str | None = None,
    concept: dict[str, Any] | None = None,
    storyboard: list[dict[str, Any]] | None = None,
    copy: dict[str, Any] | None = None,
    assets: dict[str, dict[str, Any]] | None = None,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    instructions: str = "",
    text_in_image: bool = False,
) -> CampaignRunContext:
    ctx = CampaignRunContext(
        idea=idea or "",
        product_images_b64=product_images_b64 or [],
        product_image_urls=product_image_urls or [],
        brand_kit=brand_kit,
        asset_count=_clamp_assets(asset_count),
        aspect_ratio=aspect_ratio or DEFAULT_ASPECT_RATIO,
        emit=emit or _noop_emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
        instructions=instructions,
        text_in_image=text_in_image,
    )
    if concept:
        ctx.concept = concept
    if storyboard:
        ctx.storyboard = storyboard
    if copy:
        ctx.copy = copy
    if assets:
        ctx.assets = assets
    return ctx


def _has_brand(ctx: CampaignRunContext) -> bool:
    """
    Whether there is anything for the Guardian to judge against.

    With no brand supplied there are no rules, so a verdict could only come from
    rules the model invented. Skipping is both cheaper and more honest than
    running a check that has nothing to check.
    """
    brand = ctx.brand_details
    if brand is not None and brand.any_set():
        return True
    return bool(ctx.brand_kit)


async def _guard(
    ctx: CampaignRunContext,
    target: str,
    subject_id: str | None = None,
    *,
    enabled: bool = True,
) -> GuardianVerdict | None:
    """Run the Guardian on one target, or return None if it should not run."""
    if not enabled or not _has_brand(ctx):
        return None
    try:
        await run_guardian_agent(ctx, target, subject_id)
    except Exception as exc:  # noqa: BLE001 — a failed check must not lose the work
        log.warning("guardian failed on %s/%s: %s", target, subject_id, exc)
        return None
    return ctx.guardian_verdict(target, subject_id)


async def _guarded_text_stage(
    ctx: CampaignRunContext,
    *,
    target: str,
    produce: "Callable[[str], Awaitable[None]]",
    enabled: bool,
) -> GuardianVerdict | None:
    """
    Produce a text stage, then hold it to the brand.

    A failing verdict is fed back to the agent that wrote the work and it tries
    again, the same budget the image path gets. Only when it cannot get there
    does this escalate — reporting a brand failure and continuing would put the
    verdict in the response and the off-brand concept into every later step.
    """
    await produce("")
    verdict = await _guard(ctx, target, enabled=enabled)

    attempts = 0
    while verdict is not None and verdict.failed() and attempts < MAX_QA_RETRIES:
        attempts += 1
        await produce(
            "The brand guardian rejected the previous attempt. Fix exactly these "
            f"and change nothing else:\n{verdict.as_feedback()}"
        )
        verdict = await _guard(ctx, target, enabled=enabled)

    if verdict is not None and verdict.failed():
        await ctx.raise_escalation(
            subject=target,
            target="guardian",
            verdict=verdict.model_dump(),
            attempts=attempts + 1,
        )
    return verdict


def _revised(instructions: str, revise: Any, label: str) -> str:
    """
    Instructions for a stage, with any revision request in front of them.

    Prepended rather than appended: an agent reading a long brief and then a
    small correction tends to treat the correction as an afterthought, and a
    revision is the whole point of the call when one is present.
    """
    block = render_text_revision(coerce_revision(revise), label=label)
    return "\n\n".join(x for x in (block, instructions) if x)


def _verdicts(
    guardian: GuardianVerdict | None,
    qa: dict[str, Any] | None = None,
    logo: GuardianVerdict | None = None,
) -> dict[str, Any]:
    """The `verdicts` block. Every key always present so callers need no branch."""
    return {
        "qa": qa,
        "guardian": guardian.model_dump() if guardian is not None else None,
        "logo": logo.model_dump() if logo is not None else None,
    }


async def _ensure_guardrails(
    ctx: "CampaignRunContext | VideoRunContext", *, infer: bool, supplied: Any
) -> None:
    """
    Fill in the rule set once per workflow, then leave it alone.

    Inference runs only when the caller supplied no set. Running it again on a
    resend would add rules to a set a human may already have reviewed, and the
    caller would have no way to tell which of the rules they approved.
    """
    if not infer:
        return
    existing = coerce_guardrails(supplied)
    if existing is not None and existing.rules:
        return

    # Product truth does not need a brand — what the photographs show is worth
    # enforcing on its own, and it is the check that catches a generator quietly
    # restyling the product it was given.
    if ctx.product_images_b64:
        try:
            await run_product_agent(ctx)
        except Exception as exc:  # noqa: BLE001 — a missing rule is not fatal
            log.warning("product agent failed, continuing without product rules: %s", exc)

    if not _has_brand(ctx):
        return
    try:
        await run_guardrail_agent(ctx)
    except Exception as exc:  # noqa: BLE001 — derived rules are enough to proceed
        log.warning("guardrail inference failed, using derived rules only: %s", exc)


# Envelope and echoed-input keys: present in every step result, never the thing
# a human is being asked to judge.
_NOT_REVIEWABLE = {
    "ok", "gate", "escalation", "trace", "idea", "verdicts", "guardrail_set", "overrides",
}


def _reviewable(result: dict[str, Any]) -> dict[str, Any]:
    """The part of a step result a human would actually review."""
    return {k: v for k, v in result.items() if k not in _NOT_REVIEWABLE}


def _with_gate(
    ctx: CampaignRunContext,
    result: dict[str, Any],
    *,
    stage_gate: str | None = None,
) -> dict[str, Any]:
    """
    Attach whatever stopped this step, if anything.

    A handler gate outranks a stage gate: if generate_scene_image refused to
    spend, that is the more specific thing the caller has to answer, and the
    stage never finished anyway. Escalations ride alongside rather than
    replacing the gate — they are a different question with different options.

    A stage gate carries the step's own output as its payload, because that is
    the thing under review. Keeping the result's own shape means a human's
    `approve_with_edits` payload is already the shape the next step consumes.
    """
    gate = ctx.pending_gate
    if gate is None and stage_gate:
        gate = ctx.gate_for(stage_gate, payload=_reviewable(result))
    result["gate"] = gate

    # VideoRunContext gates but does not escalate; both keys stay present so the
    # response shape does not depend on which context produced it.
    open_escalations = getattr(ctx, "open_escalations", None)
    escalations = open_escalations() if callable(open_escalations) else []
    result["escalation"] = escalations[0] if escalations else None

    # Every response carries the rule set, because the caller has to send it back
    # on the next call and cannot resend what it was never given.
    guardrails = getattr(ctx, "guardrails", None)
    result["guardrail_set"] = guardrails.model_dump() if guardrails is not None else None

    # Work that shipped over a failed check, and who said so. Reported separately
    # from verdicts: an overridden failure is not a pass, and a caller filing this
    # away should be able to find it later without re-deriving it.
    result["overrides"] = list(getattr(ctx, "overrides", []) or [])
    return result


async def step_concept(
    *,
    idea: str,
    product_images_b64: list[dict[str, str]],
    product_image_urls: list[str] | None = None,
    brand_kit: dict[str, Any] | None = None,
    asset_count: int | None = None,
    aspect_ratio: str | None = None,
    instructions: str = "",
    run_guardian: bool = True,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    infer_guardrails: bool = True,
) -> dict[str, Any]:
    require_llm_key()
    ctx = _make_ctx(
        idea=idea,
        product_images_b64=product_images_b64,
        product_image_urls=product_image_urls,
        brand_kit=brand_kit,
        asset_count=asset_count,
        aspect_ratio=aspect_ratio,
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
    )
    await _ensure_guardrails(ctx, infer=infer_guardrails, supplied=guardrail_set)

    guidance = _revised(instructions, revise, "concept")

    async def produce(correction: str) -> None:
        try:
            await run_concept_agent(
                ctx, f"{guidance}\n\n{correction}".strip() if correction else guidance
            )
        except Exception as exc:
            log.warning("concept agent failed: %s", exc)

    guardian = await _guarded_text_stage(
        ctx, target=TARGET_CONCEPT, produce=produce, enabled=run_guardian
    )

    if not ctx.concept:
        # Deterministic fallback so the workflow can continue past this node
        shot = _clamp_assets(asset_count)
        ctx.concept = {
            "theme": (idea or "Product campaign")[:120],
            "tone": "confident and commercial",
            "targetAudience": "Brand-aware shoppers",
            "visualStyle": "Clean product-led lifestyle photography",
            "shotCount": shot,
            "summary": (idea or "Campaign concept from product photos")[:400],
        }
        log.info("Using fallback concept for idea=%s", (idea or "")[:80])

    return _with_gate(
        ctx,
        {
            "ok": True,
            "concept": ctx.concept,
            "idea": ctx.idea,
            "verdicts": _verdicts(guardian),
        },
        stage_gate=GATE_CONCEPT,
    )


async def step_storyboard(
    *,
    idea: str,
    concept: dict[str, Any],
    product_images_b64: list[dict[str, str]],
    product_image_urls: list[str] | None = None,
    brand_kit: dict[str, Any] | None = None,
    asset_count: int | None = None,
    aspect_ratio: str | None = None,
    instructions: str = "",
    run_guardian: bool = True,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    infer_guardrails: bool = True,
) -> dict[str, Any]:
    require_llm_key()
    if not concept:
        raise ValueError("concept is required")
    ctx = _make_ctx(
        idea=idea,
        product_images_b64=product_images_b64,
        product_image_urls=product_image_urls,
        brand_kit=brand_kit,
        asset_count=asset_count or concept.get("shotCount"),
        aspect_ratio=aspect_ratio,
        concept=concept,
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
    )
    await _ensure_guardrails(ctx, infer=infer_guardrails, supplied=guardrail_set)

    guidance = _revised(instructions, revise, "storyboard")

    async def produce(correction: str) -> None:
        await run_storyboard_agent(
            ctx, f"{guidance}\n\n{correction}".strip() if correction else guidance
        )

    guardian = await _guarded_text_stage(
        ctx, target=TARGET_STORYBOARD, produce=produce, enabled=run_guardian
    )

    if not ctx.storyboard:
        raise RuntimeError("Storyboard Designer did not produce scenes")
    return _with_gate(
        ctx,
        {
            "ok": True,
            "concept": ctx.concept,
            "storyboard": ctx.storyboard,
            "sceneCount": len(ctx.storyboard),
            "verdicts": _verdicts(guardian),
        },
        stage_gate=GATE_STORYBOARD,
    )


async def step_scene_images(
    *,
    idea: str,
    concept: dict[str, Any],
    storyboard: list[dict[str, Any]],
    # Which scenes to generate, by id. Empty means every scene in storyboard —
    # unchanged from before this existed. Named ones restrict what this call
    # even attempts, so a second scene_image_generator node sharing the same
    # storyboard can own a different scene instead of redoing this one's work.
    scene_ids: list[str] | None = None,
    product_images_b64: list[dict[str, str]],
    product_image_urls: list[str] | None = None,
    brand_kit: dict[str, Any] | None = None,
    aspect_ratio: str | None = None,
    instructions: str = "",
    # Whether words may be rendered into the picture. See
    # CampaignRunContext.wants_text_in_image — off unless someone asked.
    text_in_image: bool = False,
    run_qa: bool = True,
    run_guardian: bool = True,
    lock_direction: bool = True,
    prior_assets: dict[str, dict[str, str]] | None = None,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    infer_guardrails: bool = True,
) -> dict[str, Any]:
    """Generate an image per storyboard scene, optionally with QA retries."""
    require_llm_key()
    if not storyboard:
        raise ValueError("storyboard is required")
    # Restricting which scenes this call generates, not merely which it is told
    # about — everything below, including the direction lock and the response
    # this call returns, only ever sees the scenes it is actually responsible
    # for. Filtered before ctx exists so nothing downstream needs its own
    # special case for "scoped or not."
    unmatched_ids: list[str] = []
    if any(str(s).strip() for s in (scene_ids or [])):
        keep, unmatched_ids = _resolve_scene_ids(scene_ids, storyboard)
        if not keep:
            # Says what the storyboard has, which the old message did not — the
            # whole difficulty here is that the two names disagree, and being
            # told only the one that failed leaves you guessing at the other.
            raise ValueError(
                f"None of the requested scene_ids ({unmatched_ids}) match this storyboard. "
                f"Its scenes are: {[str(s.get('id')) for s in storyboard]}"
            )
        if unmatched_ids:
            # Some resolved, so this node has real work to do. Failing all of it
            # over the ones that did not is worse than doing that work and
            # saying plainly which names went nowhere.
            log.warning(
                "scene_ids %s match nothing in this storyboard (%s); generating %s",
                unmatched_ids, [s.get("id") for s in storyboard], keep,
            )
        wanted = set(keep)
        storyboard = [s for s in storyboard if str(s.get("id") or "").strip() in wanted]
    ctx = _make_ctx(
        idea=idea,
        product_images_b64=product_images_b64,
        product_image_urls=product_image_urls,
        brand_kit=brand_kit,
        asset_count=len(storyboard),
        aspect_ratio=aspect_ratio,
        concept=concept or {},
        storyboard=storyboard,
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
        instructions=instructions,
        text_in_image=text_in_image,
    )

    # The first scene that lands becomes the reference the Art Director reads a
    # direction off; every later scene is then generated under it. Without this
    # the step path generated each scene in isolation, so scene 4 had nothing
    # holding it to scene 1 — the supervisor flow has always done this, and its
    # absence here was a consistency gap, not a deliberate difference.
    # Nothing to be consistent with on a single scene, and nothing to be
    # consistent for when every scene is already in hand.
    pending = [
        s for s in storyboard
        if str(s.get("id") or "").strip() not in (prior_assets or {})
    ]
    lock_direction = lock_direction and len(storyboard) > 1 and bool(pending)

    await _ensure_guardrails(ctx, infer=infer_guardrails, supplied=guardrail_set)

    assets_out: list[dict[str, Any]] = []
    for scene in storyboard:
        sid = str(scene.get("id") or "").strip()
        if not sid:
            continue
        prompt = str(scene.get("imagePrompt") or scene.get("description") or scene.get("title") or "").strip()
        if not prompt:
            prompt = f"Product marketing scene: {scene.get('title') or sid}"
        # Node-level direction, on every scene this node makes. Newly reachable:
        # the request model dropped `instructions` until now, so a human typing
        # "plain background, colder light" against an image node changed
        # nothing at all and nothing said why.
        if instructions:
            prompt = f"{prompt}\n\nDIRECTION FOR EVERY SCENE HERE:\n{instructions}"
        title = scene.get("title") or sid

        # A scene the caller already holds is passed through untouched: not
        # generated, and not re-checked either. Re-running QA and the Guardian on
        # approved work costs a call apiece and can come back differently, which
        # would escalate an asset a human already signed off.
        #
        # Unless a human asked for a change to it. Then the image they are looking
        # at becomes the source and the scene is edited rather than skipped, which
        # is the whole difference between "keep this" and "keep this, but darker".
        reused = (prior_assets or {}).get(sid)
        if reused and sid in ctx.scene_revisions:
            ctx.revision_sources[sid] = reused
            reused = None
        if reused:
            assets_out.append(
                {
                    "sceneId": sid,
                    "title": title,
                    "prompt": prompt,
                    "imageBase64": reused["data"],
                    "mimeType": reused["mime_type"],
                    "qa": None,
                    "guardian": None,
                    "logo": None,
                    "retries": 0,
                    "error": None,
                    "reused": True,
                }
            )
            ctx.assets[sid] = {
                "sceneId": sid,
                "title": title,
                "prompt": prompt,
                "imageBase64": reused["data"],
                "mimeType": reused["mime_type"],
            }
            # A reused scene is still a reference the rest can be matched to, and
            # the better one: it is the image a human actually approved.
            if lock_direction and not ctx.direction:
                try:
                    await run_art_director_agent(ctx, sid)
                except Exception as exc:  # noqa: BLE001 — a missing direction is not fatal
                    log.warning("art director failed on reused %s: %s", sid, exc)
            continue

        # A rejection from an earlier round is the one thing that changes this
        # attempt: fold it into the brief and generate again. There is no other
        # automatic correction any more — see the block below.
        rejection = ctx.approvals.retry_feedback(GATE_POST_IMAGE, sid)
        if rejection:
            prompt = f"{prompt}\n\nHUMAN REVIEW — required changes:\n{rejection}"
            ctx.bump_retry(sid)

        guardian = logo = None
        result = await ctx.generate_scene_image(
            {
                "scene_id": sid,
                "prompt": prompt,
                "title": title,
                "aspect_ratio": aspect_ratio or ctx.aspect_ratio,
            }
        )
        if result.get("ok"):
            if run_qa:
                await run_qa_agent(ctx, sid)
            # Both checks run regardless of QA, so a human reviewing the gate sees
            # the whole picture in one place rather than approving on QA alone and
            # finding a brand problem after the fact.
            guardian = await _guard(ctx, TARGET_IMAGE, sid, enabled=run_guardian)
            # Only when a mark was actually supplied. Without one there is nothing
            # to compare against, and a second vision call per scene is not free.
            logo = await _guard(
                ctx, TARGET_LOGO, sid, enabled=run_guardian and ctx.has_logo()
            )

            # One attempt, always shown — pass or fail. A generation used to keep
            # retrying itself here, up to MAX_QA_RETRIES times, and only bothered a
            # human once it had given up; a human never saw a passing image either,
            # since passing was the automatic exit. Now every attempt is the same
            # question: accept this, or say what to change. QA and the Guardian
            # still run and still inform the answer, they just no longer get to
            # give the answer themselves.
            # subject_id passed to is_gated too: without it a scoped entry like
            # post_image:scene_002 only ever matches the _all check, which it is
            # not in, and a human asking to watch one scene would get no gate at
            # all — the exact silent failure this feature exists to prevent.
            if ctx.gates.is_gated(GATE_POST_IMAGE, sid) and not ctx.approvals.approved(
                GATE_POST_IMAGE, sid
            ):
                asset = ctx.assets.get(sid) or {}
                await ctx.raise_gate(
                    gate=GATE_POST_IMAGE,
                    subject_id=sid,
                    payload={
                        "scene_id": sid,
                        "title": title,
                        "imageBase64": asset.get("imageBase64"),
                        "mimeType": asset.get("mimeType") or "image/png",
                        "prompt": prompt,
                        "qa": asset.get("qa"),
                        "guardian": guardian.model_dump() if guardian else None,
                        "logo": logo.model_dump() if logo else None,
                        "attempt": int(ctx.retry_counts.get(sid, 0)) + 1,
                    },
                    message=f"{sid} generated — approve it, or say what to change",
                )

        # Lock the direction off the first image that exists, before the rest are
        # generated. A `direction` gate fires on the next scene, not this one:
        # there is nothing to approve until a direction has been read. Skipped
        # while this scene's own result is still awaiting a decision — nothing
        # here is a reference yet.
        if (
            lock_direction and not ctx.direction and ctx.pending_gate is None
            and (ctx.assets.get(sid) or {}).get("imageBase64")
        ):
            try:
                await run_art_director_agent(ctx, sid)
            except Exception as exc:  # noqa: BLE001 — a missing direction is not fatal
                log.warning("art director failed on %s, continuing unlocked: %s", sid, exc)

        asset = ctx.assets.get(sid) or {}
        assets_out.append(
            {
                "sceneId": sid,
                "title": asset.get("title") or title,
                "prompt": asset.get("prompt") or prompt,
                "imageBase64": asset.get("imageBase64"),
                "mimeType": asset.get("mimeType") or "image/png",
                "qa": asset.get("qa"),
                "guardian": guardian.model_dump() if guardian else None,
                "logo": logo.model_dump() if logo else None,
                "retries": int(ctx.retry_counts.get(sid, 0)),
                "error": asset.get("error"),
                "needsHumanReview": bool(
                    isinstance(asset.get("qa"), dict) and not asset["qa"].get("passed")
                ),
            }
        )

        # A gate with no subject — a direction — applies to the whole run, so
        # every remaining scene would raise the identical question. Stop and ask
        # once. A gate that names a subject is usually that scene's alone, and the
        # others must still run: that is what makes `pre_image:scene_001` mean the
        # hero and not the campaign.
        #
        # post_image is the one scoped exception. pre_image and direction are
        # checked before anything is spent, so leaving a gated scene behind to let
        # an ungated one generate costs nothing extra. post_image fires only after
        # a real generation, so "leave it behind" would mean spending on scene_2
        # before a human has even seen scene_1 — which is the whole behavior this
        # gate exists to stop.
        gate = ctx.pending_gate
        if gate is not None and (
            gate.get("subject_id") is None or gate.get("gate") == GATE_POST_IMAGE
        ):
            break

    ok_count = sum(1 for a in assets_out if a.get("imageBase64"))
    # A gate stopping every scene also produces no images, and that is a question
    # for a human rather than a failure. Raising here would turn "approve this
    # first" into a 500 whenever the gate covers the whole storyboard.
    if ok_count == 0 and ctx.pending_gate is None:
        raise RuntimeError("Scene image generation produced no images")

    return _with_gate(
        ctx,
        {
            "ok": True,
            "assets": assets_out,
            "generatedCount": ok_count,
            "storyboard": ctx.storyboard,
            "concept": ctx.concept,
            # Present only when something was asked for that no scene answers
            # to. A warning in the server log is invisible to the caller that
            # wrote the wrong name.
            **({"unmatchedSceneIds": unmatched_ids} if unmatched_ids else {}),
        },
        # No stage gate for direction: it is enforced in generate_scene_image,
        # ahead of the spend it exists to control. Asking again here would ask
        # about images that already exist.
    )


async def step_qa(
    *,
    idea: str,
    concept: dict[str, Any],
    storyboard: list[dict[str, Any]],
    scene_id: str,
    image_base64: str,
    mime_type: str = "image/png",
    product_images_b64: list[dict[str, str]] | None = None,
    brand_kit: dict[str, Any] | None = None,
    run_guardian: bool = True,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    infer_guardrails: bool = True,
) -> dict[str, Any]:
    require_llm_key()
    if not scene_id:
        raise ValueError("scene_id is required")
    if not image_base64:
        raise ValueError("image_base64 is required")
    ctx = _make_ctx(
        idea=idea,
        product_images_b64=product_images_b64 or [],
        brand_kit=brand_kit,
        concept=concept or {},
        storyboard=storyboard or [],
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
    )
    ctx.assets[scene_id] = {
        "sceneId": scene_id,
        "imageBase64": image_base64,
        "mimeType": mime_type or "image/png",
        "qa": None,
    }
    await _ensure_guardrails(ctx, infer=infer_guardrails, supplied=guardrail_set)

    await run_qa_agent(ctx, scene_id)
    verdict = (ctx.assets.get(scene_id) or {}).get("qa") or {}
    guardian = await _guard(ctx, TARGET_IMAGE, scene_id, enabled=run_guardian)
    logo = await _guard(ctx, TARGET_LOGO, scene_id, enabled=run_guardian and ctx.has_logo())

    # No gate is enforced on this path, but the envelope is uniform so callers
    # never have to special-case which step they asked for.
    return _with_gate(
        ctx,
        {
            "ok": True,
            "sceneId": scene_id,
            "verdict": verdict,
            # `passed` stays the QA answer it has always been. Folding brand into
            # it would change what an existing caller's boolean means.
            "passed": bool(verdict.get("passed")),
            "verdicts": _verdicts(guardian, qa=verdict or None, logo=logo),
        },
    )


async def step_marketing(
    *,
    idea: str,
    concept: dict[str, Any],
    storyboard: list[dict[str, Any]],
    brand_kit: dict[str, Any] | None = None,
    product_images_b64: list[dict[str, str]] | None = None,
    instructions: str = "",
    run_guardian: bool = True,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    infer_guardrails: bool = True,
) -> dict[str, Any]:
    require_llm_key()
    if not concept:
        raise ValueError("concept is required")
    if not storyboard:
        raise ValueError("storyboard is required")
    ctx = _make_ctx(
        idea=idea,
        product_images_b64=product_images_b64 or [],
        brand_kit=brand_kit,
        concept=concept,
        storyboard=storyboard,
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
    )
    await _ensure_guardrails(ctx, infer=infer_guardrails, supplied=guardrail_set)

    guidance = _revised(instructions, revise, "copy")

    async def produce(correction: str) -> None:
        await run_marketing_agent(
            ctx, f"{guidance}\n\n{correction}".strip() if correction else guidance
        )

    guardian = await _guarded_text_stage(
        ctx, target=TARGET_COPY, produce=produce, enabled=run_guardian
    )

    if not ctx.copy:
        raise RuntimeError("Marketing agent did not produce copy")
    return _with_gate(
        ctx,
        {
            "ok": True,
            "copy": ctx.copy,
            "concept": ctx.concept,
            "storyboard": ctx.storyboard,
            "verdicts": _verdicts(guardian),
        },
        stage_gate=GATE_COPY,
    )


async def step_video(
    *,
    concept: dict[str, Any],
    storyboard: list[dict[str, Any]],
    marketing_copy: dict[str, Any] | None = None,
    brand_kit: dict[str, Any] | None = None,
    instructions: str = "",
    aspect_ratio: str | None = None,
    selected_scene_id: str = "",
    scene_ids: list[str] | None = None,
    target_seconds: int | None = None,
    product_images_b64: list[dict[str, str]],
    # scene_id -> {mime_type, data}: a scene's own generated hero image, when
    # the caller already has one. See VideoRunContext for what a clip does with
    # it — this step only threads it through.
    scene_images: dict[str, dict[str, str]] | None = None,
    # [{scene_id, clip_id}] this caller already rendered on an earlier call.
    # Read off local disk instead of paying for them again — see clip_store.
    prior_clips: list[dict[str, str]] | None = None,
    emit: EmitFn | None = None,
    brand_details: BrandDetails | dict | None = None,
    project_details: ProjectDetails | dict | None = None,
    hitl_gates: list[str] | None = None,
    approvals: list | None = None,
    guardrail_set: Any = None,
    escalation_decisions: list | None = None,
    revise: Any = None,
    product_details: Any = None,
    audience_segment: str | None = None,
    asset_type: str | None = None,
    scene_revisions: list | None = None,
    infer_guardrails: bool = True,
) -> dict[str, Any]:
    require_llm_key()
    # Either one scene or a list of them. A film longer than a single provider
    # clip is simply more cuts, so the list is what makes a 15s reel expressible.
    cuts = [str(s).strip() for s in (scene_ids or []) if str(s).strip()]
    if not cuts and selected_scene_id:
        cuts = [selected_scene_id.strip()]
    if not cuts:
        raise ValueError("selected_scene_id or scene_ids is required")
    selected_scene_id = selected_scene_id or cuts[0]

    # The same plan-time-name problem step_scene_images hits, with a quieter
    # failure. An id no scene answers to still cuts: scene_ids() hands the list
    # to the Director as given, so _frames_for finds no scene image for it and
    # every cut opens on the raw product photo — the film is made, it just
    # ignores the storyboard it was supposedly built from, and nothing says so.
    # Resolved here so a mismatch is either corrected or reported.
    if storyboard:
        resolved, unknown = _resolve_scene_ids(cuts, storyboard)
        if resolved:
            cuts = resolved
            if unknown:
                log.warning(
                    "video scene_ids %s match nothing in this storyboard (%s); cutting %s",
                    unknown, [s.get("id") for s in storyboard], cuts,
                )
        else:
            raise ValueError(
                f"None of the requested scene_ids ({unknown}) match this storyboard. "
                f"Its scenes are: {[str(s.get('id')) for s in storyboard]}"
            )
        if selected_scene_id not in cuts:
            selected_scene_id = cuts[0]

    # Every film goes through ffmpeg, even a one-cut one. Checked before the
    # Director runs rather than after: without this the clips are generated and
    # paid for, finalize fails on something no retry can fix, the agent burns its
    # remaining turns trying, and the caller is told "exceeded max turns" — which
    # names neither the cause nor the fix.
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is not installed, so generated clips could not be stitched into a "
            "film. Install it (e.g. `brew install ffmpeg`) and restart the service. It "
            "is already in the deployed image; this is a local-machine gap."
        )
    if not product_images_b64:
        raise ValueError("At least one product image is required")

    events: list[dict[str, Any]] = []
    _external_emit = emit

    async def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if _external_emit is not None:
            await _external_emit(event)

    ctx = VideoRunContext(
        concept=concept or {},
        storyboard=storyboard or [{"id": sid} for sid in cuts],
        copy=marketing_copy or {},
        brand_kit=brand_kit,
        instructions=instructions or "",
        aspect_ratio=aspect_ratio or "16:9",
        selected_scene_id=selected_scene_id,
        scene_ids=cuts,
        target_seconds=target_seconds,
        product_images=product_images_b64,
        scene_images=scene_images,
        prior_clips=prior_clips,
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
        hitl_gates=hitl_gates,
        approvals=approvals,
        guardrail_set=guardrail_set,
        escalation_decisions=escalation_decisions,
        scene_revisions=scene_revisions,
        product_details=product_details,
        audience_segment=audience_segment,
        asset_type=asset_type,
    )
    await _ensure_guardrails(ctx, infer=infer_guardrails, supplied=guardrail_set)

    await run_video_director(ctx)
    if not ctx.finalized and ctx.pending_gate is None:
        ready = [s for s in ctx.scene_ids() if s in ctx.clips]
        if ready:
            await ctx.finalize_video(
                {"order": ready, "summary": f"campaign film from {len(ready)} cut(s)"}
            )

    # A pre_video gate means no clip on purpose. Raising here would report the
    # refusal to spend as a failed render and lose the question with it.
    if not ctx.final_video_b64 and ctx.pending_gate is None:
        err = next((e.get("message") for e in events if e.get("type") == "error"), None)
        if err and "Anthropic HTTP" in str(err):
            log.warning("video_director produced no clip after LLM error; retrying direct path")
            await run_video_director_direct(ctx)
        if not ctx.final_video_b64:
            raise RuntimeError(err or "Video Director did not produce a clip")

    # Without _with_gate a pre_video gate would block the render and report
    # {"ok": true, "videoBase64": null} — refusing to spend but never saying why.
    return _with_gate(
        ctx,
        {
            "ok": True,
            "sceneId": selected_scene_id,
            "videoBase64": ctx.final_video_b64,
            "mimeType": ctx.final_mime or "video/mp4",
            # Send these back on the next call and those cuts are not rendered
            # again. Present even when the film finished, because a human can
            # still reject one cut and re-run, and the other cuts should
            # survive that.
            "clips": ctx.clip_refs(),
            "summary": next(
                (
                    (e.get("data") or {}).get("summary")
                    for e in reversed(events)
                    if e.get("type") == "campaign_video_complete"
                ),
                # Not "8s clip": a film is as many cuts as it needs, and the
                # fallback saying otherwise outlived the change that made it so.
                f"campaign film from {len(cuts)} cut(s)",
            ),
        },
    )


def run_sync(coro):
    """Run an async step from sync callers if needed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        raise RuntimeError("run_sync cannot be used inside a running event loop")
    return asyncio.run(coro)


async def step_critique(
    *,
    node_type: str,
    goal: str = "",
    output_summary: str = "",
    guardrail_set: dict[str, Any] | None = None,
    brand_kit: dict[str, Any] | None = None,
    prior_feedback: str = "",
    output_images_b64: list[dict[str, str]] | None = None,
    product_images_b64: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    require_llm_key()
    result = await run_critique(
        node_type=node_type,
        goal=goal,
        output_summary=output_summary,
        guardrail_set=guardrail_set,
        brand_kit=brand_kit,
        prior_feedback=prior_feedback,
        output_images_b64=output_images_b64,
        product_images_b64=product_images_b64,
    )
    return {"ok": True, **result}


async def step_product_intelligence(
    *,
    goal: str,
    brand_kit: dict[str, Any] | None = None,
    product_name: str = "",
    product_description: str = "",
    product_images_b64: list[dict[str, str]] | None = None,
    audience: str = "",
    channels: list[str] | None = None,
) -> dict[str, Any]:
    require_llm_key()
    result = await run_product_intelligence(
        goal=goal,
        brand_kit=brand_kit,
        product_name=product_name,
        product_description=product_description,
        product_images_b64=product_images_b64,
        audience=audience,
        channels=channels,
    )
    return {"ok": True, **result}


async def step_guardrail(
    *,
    goal: str,
    brand_kit: dict[str, Any] | None = None,
    product_agent_output: dict[str, Any] | None = None,
    audience: str = "",
    channels: list[str] | None = None,
) -> dict[str, Any]:
    require_llm_key()
    result = await run_guardrail_agent(
        goal=goal,
        brand_kit=brand_kit,
        product_agent_output=product_agent_output,
        audience=audience,
        channels=channels,
    )
    return {"ok": True, "guardrailSet": result, "summary": result.get("summary")}


__all__ = [
    "step_concept",
    "step_storyboard",
    "step_scene_images",
    "step_qa",
    "step_marketing",
    "step_video",
    "step_critique",
    "step_product_intelligence",
    "step_guardrail",
    "CampaignLLMError",
]
