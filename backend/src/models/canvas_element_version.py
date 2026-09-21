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
