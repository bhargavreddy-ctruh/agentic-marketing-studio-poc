"""Brand DNA — the raw record fed into LlamaIndex's Brand DNA index (Architecture.md section 3)."""
from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class BrandProfileModel(Base, TimestampMixin):
    __tablename__ = "brand_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # Raw brand facts (colors, voice, logo rules, prohibited imagery) before indexing.
    raw_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set once the Brand DNA Agent has indexed this profile into LlamaIndex.
    indexed: Mapped[bool] = mapped_column(default=False)
