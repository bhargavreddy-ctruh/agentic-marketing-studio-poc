"""One Lead's or one direct specialist call's execution record — for tracking status and retries."""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class GenerationJobModel(Base, TimestampMixin):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(32))  # "full_image" | "full_video" | "direct_fix"
    route: Mapped[str] = mapped_column(String(64))  # which Lead or specialist handled it
    status: Mapped[str] = mapped_column(String(32), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result_canvas_element_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
