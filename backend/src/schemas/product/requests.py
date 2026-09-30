"""The public contract for onboarding a Product DNA profile."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OnboardProductRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=4000)
    price: float | None = None
    discount_percent: float | None = None
    # The product's real, physical color (2026-09-30, real requirement: a genuine product fact,
    # distinct from any brand color guideline — see core/guardrails.py's _product_rules "color"
    # handling, the actual fix for a real bug where the app refused a red product as a brand-color
    # guardrail violation).
    color: str | None = Field(default=None, max_length=100)


class UpdateProductRequest(BaseModel):
    """Edit/delete a product DNA profile (2026-09-28) — patch semantics: only the fields actually
    given are changed, everything else the DNA extraction already derived stays untouched."""

    name: str | None = Field(None, min_length=1, max_length=255)
    attributes_patch: dict[str, Any] | None = None
