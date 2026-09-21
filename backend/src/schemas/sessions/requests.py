"""The public contract for starting/continuing a session. schemas/ is imported by api/ and services/,
never by repositories/ (genai_build: repositories know nothing about the API)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class StartSessionRequest(BaseModel):
    initial_message: str = Field(..., min_length=1, max_length=4000)
    # "auto" (default — existing behavior, no pauses) or "approve" (Memory.md, Phase 4: real
    # per-stage pipeline gates and per-edit staging).
    approval_mode: str = Field(default="auto", pattern="^(auto|approve)$")


class PostTurnRequest(BaseModel):
    # Exactly one of these should be set — a card pick, or free text (Architecture.md section 1d).
    picked_option_id: str | None = None
    free_text: str | None = None
    # A user-picked canvas element this turn is explicitly about (Memory.md: "reference an
    # element in chat") — grounds direct_fix against THAT element instead of whichever was most
    # recently created. Falls back to today's auto-inferred behavior when absent or stale.
    referenced_element_id: str | None = None
