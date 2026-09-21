"""The public contract for onboarding a Brand DNA profile."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OnboardBrandRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    # Free-form raw facts: colors, voice, logo rules, prohibited imagery — whatever the brand
    # actually supplies. No fixed shape enforced here; the LLM-based synthesis step (
    # guardrail_synthesizer.py) is what extracts structure, not this schema (Rules.md section 2:
    # schemas validate input shape, not business rules).
    raw_facts: dict = Field(default_factory=dict)
