from __future__ import annotations

from pydantic import BaseModel, Field


class TargetedRegenerateRequest(BaseModel):
    instruction: str | None = Field(default=None, max_length=2000)


class CommentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class DirectEditRequest(BaseModel):
    # Whatever the frontend's direct-edit tool produced (crop/recolor/retouch) — no model call,
    # so this is just the resulting reference, not an instruction.
    storage_ref: str


class CreateElementRequest(BaseModel):
    """A brand-new canvas element from an already-uploaded asset (`POST /assets` first) — the
    real backend half of "Upload Media" / "New Image" / "New Video" / "New Audio" / "Paste": the
    user is placing existing bytes onto the canvas directly, not asking a specialist to generate
    anything, so there's no prompt/instruction here at all."""
    storage_ref: str


class GroupElementRequest(BaseModel):
    """Real, live-found gap (2026-09-26): there was no way for a user to correct an element's
    product grouping after creation at all — it was write-once, set only at generation/upload
    time, sometimes wrongly (an unrelated upload silently auto-guessed into the wrong product, or
    a generation left ungrouped). `product_id: null` explicitly ungroups; a real id groups/regroups
    — "user will group it if he feels they are the same" needs exactly this, an explicit action,
    not an automatic guess."""
    product_id: str | None = None
