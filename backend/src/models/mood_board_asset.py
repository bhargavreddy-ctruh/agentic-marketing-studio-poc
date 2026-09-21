"""A real, uploaded prior-campaign asset — Architecture.md section 1b's `asset_mood_board_search`
tool has nothing to search until assets like these actually exist. Mirrors `brand_profile.py`'s
shape: the raw record, indexed separately into LlamaIndex (`mood_board_service.py`)."""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class MoodBoardAssetModel(Base, TimestampMixin):
    __tablename__ = "mood_board_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storage_ref: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(64))
    # What the asset is / what campaign it's from / why it's worth referencing — the only thing
    # actually indexed for search (LlamaIndex has no idea what pixels look like, Phase 0 scope).
    description: Mapped[str] = mapped_column(String, default="")
