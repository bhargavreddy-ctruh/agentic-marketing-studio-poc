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
