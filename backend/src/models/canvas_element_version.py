"""
Per-element version history (Architecture.md section 1c: "Per-element undo/redo — regenerating
one clip or one background shouldn't risk a manually-perfected neighboring element"). Every real
change to a `CanvasElementModel` is recorded here; undo/redo move that element's own current
version pointer through this history without ever needing to touch another element (Phase 4,
Memory.md).
"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CanvasElementVersionModel(Base, TimestampMixin):
    __tablename__ = "canvas_element_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    element_id: Mapped[str] = mapped_column(String(36), ForeignKey("canvas_elements.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    storage_ref: Mapped[str] = mapped_column(String(255))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Real, live-found gap (2026-09-21): a direct_fix edit can genuinely change WHAT KIND of asset
    # an element is, not just its content (video_editor_cutter's video_stitcher turning a still
    # image element into a video one) — but element_type used to live only on CanvasElementModel
    # itself, never per-version, so undo/redo couldn't restore the correct type alongside the
    # correct storage_ref. Default 'image' matches every element this project has ever created
    # before this column existed.
    element_type: Mapped[str] = mapped_column(String(32), default="image")
