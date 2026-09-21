"""The public contract for a Product DNA profile."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ProductProfileResponse(BaseModel):
    id: str
    name: str
    attributes: dict
    indexed: bool
    created_at: datetime
