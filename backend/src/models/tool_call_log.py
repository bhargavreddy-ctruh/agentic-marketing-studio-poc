"""
A record of one tool call — a local complement to LangSmith's own tracing, queryable without
leaving the app (e.g. "how many times did this tool fail this session").
"""
from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ToolCallLogModel(Base, TimestampMixin):
    __tablename__ = "tool_call_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    specialist_name: Mapped[str] = mapped_column(String(64))
    # Redacted preview only — never the raw args (which may carry base64 image data). See
    # Rules.md section 6 / the existing codebase's _preview_args() pattern.
    args_preview: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16))  # "ok" | "error"
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
