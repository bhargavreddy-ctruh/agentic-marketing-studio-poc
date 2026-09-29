"""The public contract for onboarding a Product DNA profile."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OnboardProductRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=4000)
    price: float | None = None
    discount_percent: float | None = None


class UpdateProductRequest(BaseModel):
    """Edit/delete a product DNA profile (2026-09-28) — patch semantics: only the fields actually
    given are changed, everything else the DNA extraction already derived stays untouched."""

    name: str | None = Field(None, min_length=1, max_length=255)
    attributes_patch: dict[str, Any] | None = None
