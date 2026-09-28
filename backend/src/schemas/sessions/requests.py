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


class UpdateGuardrailsEnabledRequest(BaseModel):
    # Per-session on/off toggle (2026-09-25, explicit user ask: "add a toggle to turn off
    # guardrails if user wants to") — same shape/pattern as `UpdateApprovalModeRequest` above.
    guardrails_enabled: bool


class PostTurnRequest(BaseModel):
    # Exactly one of these should be set — a card pick, or free text (Architecture.md section 1d).
    picked_option_id: str | None = None
    free_text: str | None = None
    # A list of user-picked canvas elements this turn is explicitly about (Memory.md: "reference an
    # element in chat") — grounds direct_fix against THESE elements instead of whichever was most
    # recently created. Falls back to today's auto-inferred behavior when absent or stale.
    referenced_element_ids: list[str] | None = None
    # Canvas Grouping (2026-09-25, revised same day: a workflow IS one campaign — grouping is by
    # real Product DNA instead) — which of the session's already-known products
    # (`session.brief["product_profile_ids"]`) this turn's generated element(s) belong to. Always
    # an EXISTING real product id, never a free-text name — a genuinely new product is created
    # through the existing chat-detection/manual-onboarding paths, not through this field. Omitted,
    # the new element inherits the referenced parent's own product (via `referenced_element_ids[0]`)
    # or whatever product this turn's own chat message gets auto-detected as being about; with
    # neither, it lands in the unassigned bucket.
    target_product_id: str | None = None
    # Real gap closed (2026-09-28, Phase 1 of the combined grouping plan): `target_product_id:
    # null`/omitted was already overloaded to mean BOTH "no explicit choice, please infer" AND (the
    # thing the frontend now needs) "explicitly do NOT inherit the referenced parent's product" —
    # `None` can't distinguish those. This is that distinct signal: when true, the parent-element
    # inheritance branch in `session_service.py`'s resolution is skipped entirely, even though a
    # parent was referenced — the new element starts fresh (chat-detection/unassigned), the same
    # as if nothing were referenced at all. Defaults false — today's inherit-by-default behavior is
    # unchanged unless the user explicitly asks otherwise.
    start_new_product: bool = False

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
