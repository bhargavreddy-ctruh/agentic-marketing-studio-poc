"""
One addressable element on the Canvas (Architecture.md section 1c) — a background, a product
composite, a video clip, an overlay. Versioned per-element so undo/redo on one element never
touches a neighboring one.
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CanvasElementModel(Base, TimestampMixin):
    __tablename__ = "canvas_elements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    element_type: Mapped[str] = mapped_column(String(32))  # "image" | "video" | "audio"
    produced_by_specialist: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # The actual asset lives on local disk (Architecture.md — generated file storage); this is a
    # reference, not the bytes themselves.
    storage_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # A staged-but-not-yet-committed edit (Memory.md, Phase 4: "approve" mode) — a real
    # regenerate/comment/direct-edit result the user must explicitly approve or reject before it
    # becomes the current version. All null in "auto" mode, where edits still apply immediately.
    pending_storage_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pending_action: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # The real compliance gate's (`services/compliance/compliance_gate.py`) last verdict for this
    # element's current version — null until it's actually been run once. Run automatically right
    # after generation (`session_service.py`), not left as a dormant, manually-triggered-only
    # endpoint nobody ever called (a real, live-found gap: the gate existed and worked, but nothing
    # in the real user-facing flow ever invoked it). False surfaces as a real UI indicator so a
    # failed check is visible, not silently swallowed.
    compliance_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
