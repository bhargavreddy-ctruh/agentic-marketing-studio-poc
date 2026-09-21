"""The public contract for onboarding a Product DNA profile."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OnboardProductRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=4000)
    price: float | None = None
    discount_percent: float | None = None
