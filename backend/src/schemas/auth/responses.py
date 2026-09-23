"""The public contract for a user — never the raw UserModel (never exposes password_hash/salt)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    username: str
    created_at: datetime
