from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MoodBoardAssetResponse(BaseModel):
    id: str
    storage_ref: str
    mime_type: str
    description: str
    created_at: datetime
