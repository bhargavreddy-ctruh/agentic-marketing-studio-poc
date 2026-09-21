"""What a caller gets back for a session — never the raw SessionModel (mappers translate)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IdeationOption(BaseModel):
    """One pickable card option — Architecture.md section 1d: bold label + one-line rationale."""

    id: str
    label: str
    description: str


class IdeationPrompt(BaseModel):
    """The propose-options-plus-free-text shape, used for both ideation and the compliance gate."""

    message: str
    options: list[IdeationOption] = []
    allow_free_text: bool = True


class SessionResponse(BaseModel):
    id: str
    status: str
    approval_mode: str
    brief: dict
    created_at: datetime
    updated_at: datetime
    next_prompt: IdeationPrompt | None = None
