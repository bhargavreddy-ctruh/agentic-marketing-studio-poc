"""A conversation/ideation session — one per user's creative-partner interaction."""
from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class SessionModel(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="ideating")
    # The evolving brief as ideation proceeds — free-form JSON is fine here since it's this
    # session's own scratch state, not a contract crossing a layer boundary.
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    brand_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    product_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # "auto" (default, existing behavior — a Lead runs its whole pipeline through with no pauses)
    # or "approve" (Memory.md, Phase 4: real per-stage pipeline gates and per-edit staging — a
    # user's explicit ask for genuine approval checkpoints, not just after-the-fact fixes).
    approval_mode: Mapped[str] = mapped_column(String(16), default="auto")
