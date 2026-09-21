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
