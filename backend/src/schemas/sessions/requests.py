"""The public contract for starting/continuing a session. schemas/ is imported by api/ and services/,
never by repositories/ (genai_build: repositories know nothing about the API)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    # "auto" (default — existing behavior, no pauses) or "approve" (Memory.md, Phase 4: real
    # per-stage pipeline gates and per-edit staging). No initial_message any more — creating the
    # session and running its first turn are now two separate calls (session_service.py's
    # `create_session` docstring explains why: the client needs the id back before it can open
    # the SSE stream, so the first turn's live events aren't lost).
    approval_mode: str = Field(default="auto", pattern="^(auto|approve)$")
    # A real, human-chosen workflow name (Tasks_Workflows.md #2) — optional; the model's own
    # default ("Untitled workflow") covers a blank/omitted title rather than rejecting the request.
    title: str | None = Field(default=None, max_length=200)


class UpdateApprovalModeRequest(BaseModel):
    # Real, live-found gap (2026-09-24, per an explicit user ask: "in chat box user should be
    # able to select the mode(auto/approve mode)") — `approval_mode` was only ever settable at
    # session CREATION (`CreateSessionRequest` above); once a session existed, the chat UI could
    # only display it, never change it. Same validation as creation, reused here rather than
    # duplicated.
    approval_mode: str = Field(pattern="^(auto|approve)$")


class PostTurnRequest(BaseModel):
    # Exactly one of these should be set — a card pick, or free text (Architecture.md section 1d).
    picked_option_id: str | None = None
    free_text: str | None = None
    # A list of user-picked canvas elements this turn is explicitly about (Memory.md: "reference an
    # element in chat") — grounds direct_fix against THESE elements instead of whichever was most
    # recently created. Falls back to today's auto-inferred behavior when absent or stale.
    referenced_element_ids: list[str] | None = None

class CampaignDetails(BaseModel):
    campaignIdea: str | None = None
    audience: str | None = None
    goal: str | None = None

class BrandDetails(BaseModel):
    voiceAndTone: str | None = None
    visualIdentity: str | None = None
    logoRules: str | None = None
    logoImage: str | None = None

class ProductDetails(BaseModel):
    name: str | None = None
    category: str | None = None
    productPhotos: list[str] | None = None
    productDescription: str | None = None

class UpdateDnaRequest(BaseModel):
    brand_dna: str | None = None
    product_dna: str | None = None
    campaignDetails: CampaignDetails | None = None
    brandDetails: BrandDetails | None = None
    productDetails: ProductDetails | None = None
