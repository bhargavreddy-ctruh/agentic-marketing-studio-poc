"""
routes.py — APIRouter for Campaign Studio multi-agent service.

Endpoints (prefixed with /campaign-studio):
  GET  /campaign-studio/                 -> health check
  POST /campaign-studio/api/run          -> run multi-agent campaign (SSE)
  POST /campaign-studio/api/video-run    -> Video Director campaign film (SSE)
  POST /campaign-studio/api/agent/concept      -> concept only (JSON)
  POST /campaign-studio/api/agent/storyboard   -> storyboard only (JSON)
  POST /campaign-studio/api/agent/scene-images -> scene images + optional QA (JSON)
  POST /campaign-studio/api/agent/qa           -> QA one scene image (JSON)
  POST /campaign-studio/api/agent/critique     -> generalized node critique (JSON)
  POST /campaign-studio/api/agent/product      -> Product Intelligence Agent (JSON)
  POST /campaign-studio/api/agent/guardrail    -> Guardrail Agent (JSON)
  POST /campaign-studio/api/agent/marketing    -> marketing copy (JSON)
  POST /campaign-studio/api/agent/video        -> 8s scene clip (JSON)
"""
from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..agent_core.brand_context import BrandDetails, ProductDetails, ProjectDetails
from ..agent_core.escalations import EscalationDecision
from ..agent_core.gates import ALL_GATES, Decision
from ..agent_core.revision import Revision, SceneRevision
from . import agent_steps
from .config import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_ASSET_COUNT,
    MAX_ASSET_COUNT,
    MAX_IMAGE_BYTES,
    MAX_PRIOR_ASSETS,
    MAX_REQUEST_IMAGE_BYTES,
    llm_configured,
    llm_health_status,
)
from .graph import run_campaign_events, run_video_events
from .llm_provider import CampaignLLMError
from .logger import get_logger

log = get_logger(__name__)

router = APIRouter(
    prefix="/campaign-studio",
    tags=["Campaign-Studio"],
)


class BrandKitPayload(BaseModel):
    profile: Optional[dict[str, Any]] = None
    kit: Optional[dict[str, Any]] = None
    name: Optional[str] = None

    class Config:
        extra = "allow"


class RunCampaignRequest(BaseModel):
    idea: str = Field(..., min_length=3, max_length=4000)
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(
        default_factory=list,
        description="Optional list of {mime_type, data} product images",
        max_length=8,
    )
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    asset_count: int = Field(default=DEFAULT_ASSET_COUNT, ge=1, le=MAX_ASSET_COUNT)
    aspect_ratio: str = Field(default=DEFAULT_ASPECT_RATIO)

    @field_validator("idea")
    @classmethod
    def strip_idea(cls, v: str) -> str:
        return v.strip()


class PriorAsset(BaseModel):
    """
    An image the caller already has and does not want made again.

    The point is not only cost. Regenerating a scene produces a *different*
    image, so a caller resuming after an approval would silently replace the
    asset a human signed off with one nobody has seen.
    """

    scene_id: str = Field(..., min_length=1, max_length=64)
    image_base64: Optional[str] = None
    image_url: Optional[str] = None
    mime_type: str = Field(default="image/png")


class SceneImageRef(BaseModel):
    scene_id: str = Field(..., min_length=1, max_length=64)
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    mime_type: Optional[str] = "image/jpeg"


class PriorClipRef(BaseModel):
    """
    A clip this caller already rendered, named rather than resent.

    prior_assets carries an image back as base64 because an image fits in a
    request. A clip does not — three of them would exceed the whole 32 MB
    budget — so the bytes stay in the local clip store and only the id it
    filed them under travels. Anything the store does not recognise is simply
    rendered again.
    """

    scene_id: str = Field(..., min_length=1, max_length=64)
    clip_id: str = Field(..., min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


class RunVideoRequest(BaseModel):
    concept: dict[str, Any] = Field(default_factory=dict)
    storyboard: list[dict[str, Any]] = Field(default_factory=list)
    marketing_copy: dict[str, Any] = Field(default_factory=dict)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    instructions: str = Field(default="", max_length=4000)
    aspect_ratio: str = Field(default="16:9")
    selected_scene_id: str = Field(default="", max_length=64)
    # Scenes to cut, in playback order. A clip cannot exceed the provider's
    # longest duration, so a longer film is more cuts, not a longer clip.
    scene_ids: list[str] = Field(default_factory=list, max_length=12)
    # Roughly how long the finished film should run. Guidance, not a contract:
    # the Director chooses the cuts and says so if the target is unreachable.
    target_seconds: Optional[int] = Field(default=None, ge=2, le=120)
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)


class AgentConceptRequest(BaseModel):
    idea: str = Field(..., min_length=3, max_length=4000)
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    asset_count: int = Field(default=DEFAULT_ASSET_COUNT, ge=1, le=MAX_ASSET_COUNT)
    aspect_ratio: str = Field(default=DEFAULT_ASPECT_RATIO)
    # Carries a human's reason when they reject a concept at its gate, so the
    # retry produces something different instead of re-rolling the same brief.
    instructions: str = Field(default="", max_length=2000)
    # Hold this stage to the brand. Skipped automatically when no brand is
    # supplied — there would be no rules to judge against.
    run_guardian: bool = True

    @field_validator("idea")
    @classmethod
    def strip_idea(cls, v: str) -> str:
        return v.strip()


class AgentStoryboardRequest(BaseModel):
    idea: str = Field(default="", max_length=4000)
    concept: dict[str, Any] = Field(...)
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    asset_count: Optional[int] = Field(default=None, ge=1, le=MAX_ASSET_COUNT)
    aspect_ratio: str = Field(default=DEFAULT_ASPECT_RATIO)
    instructions: str = Field(default="", max_length=2000)
    # Hold this stage to the brand. Skipped automatically when no brand is
    # supplied — there would be no rules to judge against.
    run_guardian: bool = True


class AgentSceneImagesRequest(BaseModel):
    idea: str = Field(default="", max_length=4000)
    concept: dict[str, Any] = Field(default_factory=dict)
    storyboard: list[dict[str, Any]] = Field(..., min_length=1)
    # Which of the storyboard's scenes this call should generate. Empty means
    # every scene — the original, still-default behavior. Named explicitly so
    # two scene_image_generator nodes sharing one storyboard can each own a
    # disjoint subset instead of each generating every scene in it: without
    # this, a "hero" branch and a "social" branch both wired to the same
    # 2-scene storyboard each produced both scenes, doubling the real cost of
    # a graph that meant to split the work, not duplicate it.
    scene_ids: list[str] = Field(default_factory=list, max_length=16)
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    aspect_ratio: str = Field(default=DEFAULT_ASPECT_RATIO)
    # Direction for this node in plain language. Every other step endpoint has
    # taken this from the start; this one did not, and the caller was sending it
    # anyway — Pydantic ignores an unknown field, so a human typing "plain
    # background, colder light" against an image node had it silently dropped.
    instructions: str = Field(default="", max_length=2000)
    # Whether words may be rendered INTO the picture. Off by default: a
    # generator adds a tagline, a price sticker or a garbled watermark on its
    # own initiative, and unrequested text is the most common way an otherwise
    # usable asset becomes unusable. On only when someone asked for it.
    text_in_image: bool = False
    run_qa: bool = True
    # Read a visual direction off the first image and generate the rest under it.
    # Costs one extra agent turn; without it scenes are unrelated to each other.
    lock_direction: bool = True
    # Scenes the caller already has. Not regenerated, not re-checked.
    prior_assets: list[PriorAsset] = Field(default_factory=list, max_length=MAX_PRIOR_ASSETS)
    # Hold this stage to the brand. Skipped automatically when no brand is
    # supplied — there would be no rules to judge against.
    run_guardian: bool = True


class AgentQaRequest(BaseModel):
    idea: str = Field(default="", max_length=4000)
    concept: dict[str, Any] = Field(default_factory=dict)
    storyboard: list[dict[str, Any]] = Field(default_factory=list)
    scene_id: str = Field(..., min_length=1, max_length=64)
    image_base64: str = Field(..., min_length=8)
    mime_type: str = Field(default="image/png")
    # Accepted here as everywhere else. Without it a caller working from urls
    # could QA an image with no product to compare it against — which is most of
    # what this check is for.
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    # Hold this stage to the brand. Skipped automatically when no brand is
    # supplied — there would be no rules to judge against.
    run_guardian: bool = True


class AgentMarketingRequest(BaseModel):
    idea: str = Field(default="", max_length=4000)
    concept: dict[str, Any] = Field(...)
    storyboard: list[dict[str, Any]] = Field(..., min_length=1)
    # Accepted here as everywhere else, so a url-driven caller can still write
    # copy with the product in front of it.
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    instructions: str = Field(default="", max_length=2000)
    # Hold this stage to the brand. Skipped automatically when no brand is
    # supplied — there would be no rules to judge against.
    run_guardian: bool = True


class AgentVideoRequest(BaseModel):
    concept: dict[str, Any] = Field(default_factory=dict)
    storyboard: list[dict[str, Any]] = Field(default_factory=list)
    marketing_copy: dict[str, Any] = Field(default_factory=dict)
    brand_kit: Optional[dict[str, Any]] = None
    brand_details: Optional[BrandDetails] = None
    project_details: Optional[ProjectDetails] = None
    hitl_gates: list[str] = Field(default_factory=list, max_length=32)
    approvals: list[Decision] = Field(default_factory=list, max_length=64)
    # The rule set the Guardian judges against. Resend it verbatim on every
    # call: it can carry human amendments that exist nowhere else.
    guardrail_set: Optional[dict[str, Any]] = None
    # Derive extra rules on the first call of a workflow. Ignored once a set is
    # supplied — re-inferring would add rules to one a human already reviewed.
    infer_guardrails: bool = True
    # Human answers to escalations raised on an earlier call: ship anyway,
    # or rewrite the rule that keeps failing.
    escalation_decisions: list[EscalationDecision] = Field(
        default_factory=list, max_length=64
    )
    # Change work that already exists, in words. `revise` carries the previous
    # output of a text stage; `scene_revisions` names images to edit.
    revise: Optional[Revision] = None
    # What is being sold. Domain-neutral: attributes and options carry whatever
    # is true of it, so a subscription and a sofa use the same shape.
    product_details: Optional[ProductDetails] = None
    # What this call is making, so segment- and asset-scoped rules can apply.
    audience_segment: Optional[str] = Field(default=None, max_length=80)
    asset_type: Optional[str] = Field(default=None, max_length=80)
    scene_revisions: list[SceneRevision] = Field(default_factory=list, max_length=16)
    instructions: str = Field(default="", max_length=4000)
    aspect_ratio: str = Field(default="16:9")
    selected_scene_id: str = Field(default="", max_length=64)
    # Scenes to cut, in playback order. A clip cannot exceed the provider's
    # longest duration, so a longer film is more cuts, not a longer clip.
    scene_ids: list[str] = Field(default_factory=list, max_length=12)
    # Roughly how long the finished film should run. Guidance, not a contract:
    # the Director chooses the cuts and says so if the target is unreachable.
    target_seconds: Optional[int] = Field(default=None, ge=2, le=120)
    product_image_urls: list[str] = Field(default_factory=list, max_length=8)
    product_images_base64: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    # A generated hero image for a scene, if the caller already has one — from
    # scene_image_generator, upstream in the same graph. Without this, every
    # clip animated from the same raw product photo regardless of what its own
    # scene described, so a storyboard that said "clean flat-lay" for one cut
    # and "on-model lifestyle shot" for the next produced two clips that opened
    # on an identical frame. Given one, a clip opens on what its own scene
    # actually depicts; a scene with none falls back to the product photo
    # exactly as before.
    scene_images: list[SceneImageRef] = Field(default_factory=list, max_length=16)
    # Cuts already rendered on an earlier call, by id. A `pre_video` gate means
    # one request per scene, and without these every request re-rendered every
    # cut it had already produced.
    prior_clips: list[PriorClipRef] = Field(default_factory=list, max_length=16)


@router.get("/")
async def health():
    return {
        "service": "campaign-studio",
        "status": "ok",
        "llm": llm_health_status(),
        "llmConfigured": llm_configured(),
    }


async def _hydrate_images_from_urls(urls: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in urls[:6]:
            try:
                r = await client.get(url)
                if r.status_code != 200 or len(r.content) < 500:
                    continue
                ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
                if ct == "image/jpg":
                    ct = "image/jpeg"
                out.append({"mime_type": ct, "data": base64.b64encode(r.content).decode()})
            except Exception as exc:
                log.warning("Failed to hydrate product image url: %s", exc)
    return out


def _normalize_b64_images(items: list[dict[str, str]]) -> list[dict[str, str]]:
    images_b64: list[dict[str, str]] = []
    for item in items:
        data = item.get("data") or item.get("imageBase64")
        mime = item.get("mime_type") or item.get("mimeType") or "image/jpeg"
        if data:
            if "," in data and data.startswith("data:"):
                data = data.split(",", 1)[1]
            if mime == "image/jpg":
                mime = "image/jpeg"
            images_b64.append({"mime_type": mime, "data": data})
    return images_b64


async def _resolve_product_images(
    urls: list[str],
    b64_items: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Product photos as bytes, checked against the payload budget.

    Enforced here rather than at each endpoint so a new endpoint cannot forget:
    every path into Campaign Studio resolves its images through this function.
    Callers that also carry prior assets, a logo, or an image under review
    re-check the total with those groups included.
    """
    images_b64 = _normalize_b64_images(b64_items)
    if not images_b64 and urls:
        images_b64 = await _hydrate_images_from_urls(urls)
    _enforce_payload_budget(("product_images", images_b64))
    return images_b64


def _decoded_size(data: str) -> int:
    """Bytes a base64 string decodes to, without decoding it."""
    text = (data or "").strip()
    if not text:
        return 0
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    padding = text.count("=", -2)
    return max(0, (len(text) * 3) // 4 - padding)


def _enforce_payload_budget(*groups: tuple[str, list[dict[str, str]]]) -> None:
    """
    Refuse an oversized request with a message that says what to drop.

    Measured after the images are resolved, so a caller that sent urls is judged
    on what was actually fetched. Raised as 413 rather than left to fail deeper:
    without this the worker dies on memory or the load balancer cuts the
    connection, and the caller gets a closed socket instead of a reason.
    """
    total = 0
    breakdown: list[str] = []
    for label, items in groups:
        group_total = 0
        for index, item in enumerate(items or []):
            size = _decoded_size(item.get("data") or "")
            if size > MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"{label}[{index}] is {size / 1_048_576:.1f} MB, over the "
                        f"{MAX_IMAGE_BYTES / 1_048_576:.0f} MB per-image limit. "
                        "Send a smaller or more compressed image."
                    ),
                )
            group_total += size
        if group_total:
            breakdown.append(f"{label} {group_total / 1_048_576:.1f} MB")
        total += group_total

    if total > MAX_REQUEST_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Request images total {total / 1_048_576:.1f} MB "
                f"({', '.join(breakdown)}), over the "
                f"{MAX_REQUEST_IMAGE_BYTES / 1_048_576:.0f} MB limit. Send fewer "
                "product photos, or drop prior_assets you do not need this step to reuse."
            ),
        )


async def _resolve_prior_assets(items: list["PriorAsset"]) -> dict[str, dict[str, str]]:
    """
    Already-approved images the caller is carrying forward, keyed by scene.

    Accepts a url or base64 the same way product images do; a url is fetched here
    so nothing downstream has to care which was sent.
    """
    resolved: dict[str, dict[str, str]] = {}
    for item in (items or [])[:MAX_PRIOR_ASSETS]:
        scene_id = (item.scene_id or "").strip()
        if not scene_id or scene_id in resolved:
            continue

        data = (item.image_base64 or "").strip()
        mime = item.mime_type or "image/png"
        if data:
            if data.startswith("data:") and "," in data:
                data = data.split(",", 1)[1]
        elif item.image_url:
            fetched = await _hydrate_images_from_urls([item.image_url])
            if not fetched:
                log.warning("Could not fetch prior asset for %s; it will be regenerated", scene_id)
                continue
            data, mime = fetched[0]["data"], fetched[0]["mime_type"]
        else:
            continue

        resolved[scene_id] = {
            "mime_type": "image/jpeg" if mime == "image/jpg" else mime,
            "data": data,
        }
    return resolved


async def _resolve_logo(brand: Optional[BrandDetails]) -> Optional[BrandDetails]:
    """
    Fetch a logo supplied as a URL so the Guardian can actually look at it.

    Done here rather than deeper in because this is the layer that already knows
    how to fetch. A logo that stays a URL is invisible to the check: it would
    compare a render against the words "a logo exists", which is precisely how a
    hallucinated wordmark passes.
    """
    if brand is None or brand.logo is None:
        return brand
    if brand.logo.viewable() is not None or not brand.logo.url:
        return brand

    fetched = await _hydrate_images_from_urls([brand.logo.url])
    if not fetched:
        log.warning("Could not fetch brand logo from url; logo check will be skipped")
        return brand

    # Copy rather than mutate: the request model is the caller's, and a later
    # handler reading brand.logo.url should still see what it sent.
    updated = brand.model_copy(deep=True)
    updated.logo.base64 = fetched[0]["data"]
    updated.logo.mime_type = fetched[0]["mime_type"]
    return updated


async def _run_step(
    step: Callable[..., Awaitable[dict[str, Any]]],
    *,
    stream: bool,
    **kwargs: Any,
):
    """
    Run one step endpoint, exposing the agent trace either way.

    The step functions take an `emit` callback; A3a made those events worth
    reading. Collect them and the caller gets `trace` on a normal JSON
    response; stream them and the caller watches the agents work, with the
    result arriving as the final event. Same events, two presentations —
    a step that takes 15 seconds generating an image should not have to be
    watched in silence, and a caller that only wants the answer should not
    have to speak SSE.
    """
    if not stream:
        events: list[dict[str, Any]] = []

        async def collect(event: dict[str, Any]) -> None:
            events.append(event)

        result = await step(emit=collect, **kwargs)
        if isinstance(result, dict):
            result.setdefault("trace", events)
        return result

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def push(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def drive() -> None:
        try:
            result = await step(emit=push, **kwargs)
            await queue.put({"type": "step_complete", "agent": "system", "data": result})
        except (CampaignLLMError, ValueError, RuntimeError) as exc:
            await queue.put({"type": "error", "agent": "system", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.error("streamed step failed: %s", exc, exc_info=True)
            await queue.put({"type": "error", "agent": "system", "message": str(exc)})
        finally:
            await queue.put(None)

    async def body():
        task = asyncio.create_task(drive())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _require_llm() -> None:
    if not llm_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM API_KEY is not configured. Set PROVIDER, API_KEY, and MODEL "
                "in services/config.ini [LLM]"
            ),
        )


async def _hydrate_scene_images(scenes: list[SceneImageRef]) -> dict[str, dict[str, str]]:
    """Build scene_id -> {mime_type, data} from URLs and/or inline base64."""
    out: dict[str, dict[str, str]] = {}
    urls_to_fetch: list[tuple[str, str]] = []  # (scene_id, url)

    for scene in scenes:
        sid = scene.scene_id.strip()
        if not sid:
            continue
        if scene.image_base64:
            data = scene.image_base64
            if "," in data and data.startswith("data:"):
                data = data.split(",", 1)[1]
            mime = scene.mime_type or "image/jpeg"
            if mime == "image/jpg":
                mime = "image/jpeg"
            out[sid] = {"mime_type": mime, "data": data}
        elif scene.image_url:
            urls_to_fetch.append((sid, scene.image_url))

    if urls_to_fetch:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for sid, url in urls_to_fetch:
                try:
                    r = await client.get(url)
                    if r.status_code != 200 or len(r.content) < 500:
                        log.warning("Scene image fetch failed sid=%s status=%s", sid, r.status_code)
                        continue
                    ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
                    if ct == "image/jpg":
                        ct = "image/jpeg"
                    out[sid] = {"mime_type": ct, "data": base64.b64encode(r.content).decode()}
                except Exception as exc:
                    log.warning("Failed to hydrate scene image %s: %s", sid, exc)

    return out


@router.post(
    "/api/run",
    summary="Run multi-agent campaign generation (SSE)",
    responses={
        200: {
            "description": "SSE stream of campaign agent events",
            "content": {"text/event-stream": {}},
        }
    },
)
async def run_campaign(body: RunCampaignRequest):
    if not body.idea:
        raise HTTPException(status_code=400, detail="idea is required")
    if not body.product_image_urls and not body.product_images_base64:
        raise HTTPException(status_code=400, detail="At least one product image is required")
    _require_llm()

    images_b64 = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    if not images_b64:
        raise HTTPException(status_code=400, detail="Could not load any product images")

    async def stream():
        def send(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        try:
            async for event in run_campaign_events(
                idea=body.idea,
                product_image_urls=body.product_image_urls,
                product_images_b64=images_b64,
                brand_kit=body.brand_kit,
                brand_details=await _resolve_logo(body.brand_details),
                project_details=body.project_details,
                asset_count=body.asset_count,
                aspect_ratio=body.aspect_ratio,
            ):
                yield send(event)
        except Exception as exc:
            log.error("SSE campaign stream failed: %s", exc, exc_info=True)
            yield send({"type": "error", "agent": "system", "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/api/video-run",
    summary="Run Video Director to produce a stitched campaign film (SSE)",
    responses={
        200: {
            "description": "SSE stream of Video Director events ending in campaign_video_complete",
            "content": {"text/event-stream": {}},
        }
    },
)
async def run_campaign_video(body: RunVideoRequest):
    _require_llm()
    if not body.selected_scene_id.strip():
        raise HTTPException(status_code=400, detail="selected_scene_id is required")

    product_images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    if not product_images:
        raise HTTPException(
            status_code=400,
            detail="At least one product image is required as the video start/end frame",
        )

    storyboard = body.storyboard or [
        {"id": body.selected_scene_id, "title": body.selected_scene_id}
    ]

    async def stream():
        def send(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        try:
            async for event in run_video_events(
                concept=body.concept,
                storyboard=storyboard,
                copy=body.marketing_copy,
                brand_kit=body.brand_kit,
                instructions=body.instructions,
                aspect_ratio=body.aspect_ratio,
                selected_scene_id=body.selected_scene_id.strip(),
            scene_ids=body.scene_ids,
            target_seconds=body.target_seconds,
                product_images=product_images,
            ):
                yield send(event)
        except Exception as exc:
            log.error("SSE video-run stream failed: %s", exc, exc_info=True)
            yield send({"type": "error", "agent": "system", "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Creative Studio per-agent JSON endpoints ─────────────────────────────────


@router.post("/api/agent/concept", summary="Creative Director concept only (JSON)")
async def agent_concept(body: AgentConceptRequest, stream: bool = False):
    _require_llm()
    images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    if not images:
        raise HTTPException(status_code=400, detail="At least one product image is required")
    try:
        return await _run_step(
            agent_steps.step_concept,
            stream=stream,
            brand_details=await _resolve_logo(body.brand_details),
            project_details=body.project_details,
            hitl_gates=body.hitl_gates,
            approvals=body.approvals,
            idea=body.idea,
            product_images_b64=images,
            product_image_urls=body.product_image_urls,
            brand_kit=body.brand_kit,
            asset_count=body.asset_count,
            aspect_ratio=body.aspect_ratio,
            instructions=body.instructions,
            run_guardian=body.run_guardian,
            guardrail_set=body.guardrail_set,
            infer_guardrails=body.infer_guardrails,
            escalation_decisions=body.escalation_decisions,
            revise=body.revise,
            product_details=body.product_details,
            audience_segment=body.audience_segment,
            asset_type=body.asset_type,
            scene_revisions=body.scene_revisions,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent concept failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/storyboard", summary="Storyboard Designer only (JSON)")
async def agent_storyboard(body: AgentStoryboardRequest, stream: bool = False):
    _require_llm()
    images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    try:
        return await _run_step(
            agent_steps.step_storyboard,
            stream=stream,
            brand_details=await _resolve_logo(body.brand_details),
            project_details=body.project_details,
            hitl_gates=body.hitl_gates,
            approvals=body.approvals,
            idea=body.idea,
            concept=body.concept,
            product_images_b64=images,
            product_image_urls=body.product_image_urls,
            brand_kit=body.brand_kit,
            asset_count=body.asset_count,
            aspect_ratio=body.aspect_ratio,
            instructions=body.instructions,
            run_guardian=body.run_guardian,
            guardrail_set=body.guardrail_set,
            infer_guardrails=body.infer_guardrails,
            escalation_decisions=body.escalation_decisions,
            revise=body.revise,
            product_details=body.product_details,
            audience_segment=body.audience_segment,
            asset_type=body.asset_type,
            scene_revisions=body.scene_revisions,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent storyboard failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/scene-images", summary="Generate scene images with optional QA (JSON)")
async def agent_scene_images(body: AgentSceneImagesRequest, stream: bool = False):
    _require_llm()
    images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    if not images:
        raise HTTPException(status_code=400, detail="At least one product image is required")

    brand = await _resolve_logo(body.brand_details)
    prior = await _resolve_prior_assets(body.prior_assets)
    logo = (brand.logo.viewable() if brand is not None and brand.logo is not None else None)
    _enforce_payload_budget(
        ("product_images", images),
        ("prior_assets", list(prior.values())),
        ("brand logo", [logo] if logo else []),
    )

    try:
        return await _run_step(
            agent_steps.step_scene_images,
            stream=stream,
            prior_assets=prior,
            brand_details=brand,
            project_details=body.project_details,
            hitl_gates=body.hitl_gates,
            approvals=body.approvals,
            idea=body.idea,
            concept=body.concept,
            storyboard=body.storyboard,
            scene_ids=body.scene_ids,
            product_images_b64=images,
            product_image_urls=body.product_image_urls,
            brand_kit=body.brand_kit,
            aspect_ratio=body.aspect_ratio,
            instructions=body.instructions,
            text_in_image=body.text_in_image,
            run_qa=body.run_qa,
            lock_direction=body.lock_direction,
            run_guardian=body.run_guardian,
            guardrail_set=body.guardrail_set,
            infer_guardrails=body.infer_guardrails,
            escalation_decisions=body.escalation_decisions,
            revise=body.revise,
            product_details=body.product_details,
            audience_segment=body.audience_segment,
            asset_type=body.asset_type,
            scene_revisions=body.scene_revisions,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent scene-images failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/qa", summary="QA check one scene image (JSON)")
async def agent_qa(body: AgentQaRequest, stream: bool = False):
    _require_llm()
    data = body.image_base64
    if "," in data and data.startswith("data:"):
        data = data.split(",", 1)[1]
    images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    # The generated image is the largest thing in this request and was the one
    # image never measured: QA resolved its product photos by hand and so skipped
    # the budget every other endpoint enforces.
    _enforce_payload_budget(
        ("product_images", images),
        ("image under review", [{"data": data}]),
    )
    try:
        return await _run_step(
            agent_steps.step_qa,
            stream=stream,
            brand_details=await _resolve_logo(body.brand_details),
            project_details=body.project_details,
            hitl_gates=body.hitl_gates,
            approvals=body.approvals,
            idea=body.idea,
            concept=body.concept,
            storyboard=body.storyboard,
            scene_id=body.scene_id,
            image_base64=data,
            mime_type=body.mime_type,
            product_images_b64=images,
            brand_kit=body.brand_kit,
            run_guardian=body.run_guardian,
            guardrail_set=body.guardrail_set,
            infer_guardrails=body.infer_guardrails,
            escalation_decisions=body.escalation_decisions,
            revise=body.revise,
            product_details=body.product_details,
            audience_segment=body.audience_segment,
            asset_type=body.asset_type,
            scene_revisions=body.scene_revisions,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent qa failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/critique", summary="Critique a node output against guardrails (JSON)")
async def agent_critique(body: AgentCritiqueRequest):
    _require_llm()
    try:
        return await agent_steps.step_critique(
            node_type=body.node_type,
            goal=body.goal,
            output_summary=body.output_summary,
            guardrail_set=body.guardrail_set,
            brand_kit=body.brand_kit,
            prior_feedback=body.prior_feedback,
            output_images_b64=_normalize_b64_images(body.output_images_base64),
            product_images_b64=_normalize_b64_images(body.product_images_base64),
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent critique failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/product", summary="Product Intelligence Agent (JSON)")
async def agent_product(body: AgentProductIntelRequest):
    _require_llm()
    goal = (body.goal or "").strip()
    if len(goal) < 3:
        goal = (
            (body.product_description or "").strip()
            or (body.product_name or "").strip()
            or "Analyze the product photo(s) and derive marketing constraints for a brand campaign."
        )
    try:
        return await agent_steps.step_product_intelligence(
            goal=goal,
            brand_kit=body.brand_kit,
            product_name=body.product_name,
            product_description=body.product_description,
            product_images_b64=_normalize_b64_images(body.product_images_base64),
            audience=body.audience,
            channels=body.channels,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent product intelligence failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/guardrail", summary="Guardrail Agent (JSON)")
async def agent_guardrail(body: AgentGuardrailRequest):
    _require_llm()
    goal = (body.goal or "").strip() or "Apply brand and legal guardrails for this campaign."
    try:
        return await agent_steps.step_guardrail(
            goal=goal,
            brand_kit=body.brand_kit,
            product_agent_output=body.product_agent_output,
            audience=body.audience,
            channels=body.channels,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent guardrail failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/marketing", summary="Marketing copy only (JSON)")
async def agent_marketing(body: AgentMarketingRequest, stream: bool = False):
    _require_llm()
    images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    try:
        return await _run_step(
            agent_steps.step_marketing,
            stream=stream,
            brand_details=await _resolve_logo(body.brand_details),
            project_details=body.project_details,
            hitl_gates=body.hitl_gates,
            approvals=body.approvals,
            idea=body.idea,
            concept=body.concept,
            storyboard=body.storyboard,
            brand_kit=body.brand_kit,
            product_images_b64=images,
            instructions=body.instructions,
            run_guardian=body.run_guardian,
            guardrail_set=body.guardrail_set,
            infer_guardrails=body.infer_guardrails,
            escalation_decisions=body.escalation_decisions,
            revise=body.revise,
            product_details=body.product_details,
            audience_segment=body.audience_segment,
            asset_type=body.asset_type,
            scene_revisions=body.scene_revisions,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent marketing failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/agent/video", summary="Video Director 8s clip (JSON)")
async def agent_video(body: AgentVideoRequest, stream: bool = False):
    _require_llm()
    images = await _resolve_product_images(body.product_image_urls, body.product_images_base64)
    if not images:
        raise HTTPException(status_code=400, detail="At least one product image is required")
    scene_images = await _hydrate_scene_images(body.scene_images)
    _enforce_payload_budget(
        ("product_images", images),
        ("scene_images", list(scene_images.values())),
    )
    try:
        return await _run_step(
            agent_steps.step_video,
            stream=stream,
            brand_details=await _resolve_logo(body.brand_details),
            project_details=body.project_details,
            hitl_gates=body.hitl_gates,
            approvals=body.approvals,
            concept=body.concept,
            storyboard=body.storyboard or [
                {"id": sid} for sid in (body.scene_ids or [body.selected_scene_id]) if sid
            ],
            marketing_copy=body.marketing_copy,
            brand_kit=body.brand_kit,
            instructions=body.instructions,
            aspect_ratio=body.aspect_ratio,
            selected_scene_id=body.selected_scene_id.strip(),
            scene_ids=body.scene_ids,
            target_seconds=body.target_seconds,
            product_images_b64=images,
            scene_images=scene_images,
            prior_clips=[c.model_dump() for c in body.prior_clips],
            guardrail_set=body.guardrail_set,
            infer_guardrails=body.infer_guardrails,
            escalation_decisions=body.escalation_decisions,
            revise=body.revise,
            product_details=body.product_details,
            audience_segment=body.audience_segment,
            asset_type=body.asset_type,
            scene_revisions=body.scene_revisions,
        )
    except (CampaignLLMError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("agent video failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
