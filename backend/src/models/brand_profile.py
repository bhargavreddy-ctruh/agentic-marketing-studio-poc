"""Brand DNA — the raw record fed into LlamaIndex's Brand DNA index (Architecture.md section 3)."""
from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class BrandProfileModel(Base, TimestampMixin):
    __tablename__ = "brand_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Tasks_Workflows.md #3 — nullable for the same "no migration tool" reason as
    # SessionModel.user_id; every brand onboarded through the new authenticated flow always sets
    # this. Retrieval at the RAG layer (brand_kit_lookup) is NOT scoped by this column yet — a
    # real, disclosed, deliberate scope cut (see Tasks_Workflows.md #3's own notes on why).
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # Raw brand facts (colors, voice, logo rules, prohibited imagery) before indexing.
    raw_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    logo_storage_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    font_storage_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set once the Brand DNA Agent has indexed this profile into LlamaIndex.
    indexed: Mapped[bool] = mapped_column(default=False)
