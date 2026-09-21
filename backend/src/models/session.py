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
    # The last turn's real IdeationPrompt (message/options/allow_free_text), persisted so a plain
    # GET can honestly reproduce what the client last saw — previously this was computed only
    # per-turn and handed back in that same response, so a page refresh or the "refresh" button
    # while paused at any gate (HITL or ordinary ideation) lost the prompt text entirely even
    # though brief.video_stage/narrative_plan/scene_plan survived (a real, disclosed gap from
    # Phase 4d). None once a turn produces no prompt (e.g. "completed").
    next_prompt_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
