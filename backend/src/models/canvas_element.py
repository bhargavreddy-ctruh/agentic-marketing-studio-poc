"""
One addressable element on the Canvas (Architecture.md section 1c) — a background, a product
composite, a video clip, an overlay. Versioned per-element so undo/redo on one element never
touches a neighboring one.
"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String
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
    # element's current version: "running" | "passed" | "failed". Defaults to "running" the
    # instant an element is created — the gate is kicked off as a genuine background task
    # (session_service.py), not awaited before the turn's own response returns, so the element
    # shows up on canvas immediately with a real "running" state instead of only becoming visible
    # once QA has already finished (a real, live-found UX gap: 2026-09-19/21 — the gate itself
    # existed and worked, but nothing in the real user-facing flow ever invoked it, and then
    # invoking it synchronously gave no visible "in progress" window at all). "failed" also covers
    # a real infra error running the check itself — an honest, visible flag rather than a silent
    # "unknown" that could look identical to "passed" (see the background task's own docstring).
    compliance_status: Mapped[str] = mapped_column(String(16), default="running")
    ad_spec_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_zone_pct: Mapped[float | None] = mapped_column(nullable=True)

    # Canvas Grouping (2026-09-25, revised same day per explicit user correction: "in each workflow
    # there can be only one campaign... the grouping would be according to the product id, not the
    # campaign id"). A workflow/session IS one campaign — there's no separate campaign entity to
    # track here. Grouping is by real Product DNA instead: `product_id` is a real
    # `ProductProfileModel.id` (never a free-text/ad-hoc value), resolved in
    # `services/orchestration/session_service.py` from, in order, an explicit pick, the referenced
    # parent element's own product, or whatever product this turn's own chat-detection pipeline
    # (`ProductDnaService.upsert_product_from_chat`, already running every turn for Product DNA)
    # resolved. `product_name` is denormalized here purely so the canvas can label a frame without
    # an extra fetch per element. All nullable — an element with no real product signal lands in the
    # frontend's flat, honestly-labeled "Unassigned" bucket, never a fabricated grouping.
    # `parent_element_id` unchanged from the earlier pass: set server-side from the turn's own
    # `referenced_element_ids[0]`, never a separate client-sent field.
    product_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_element_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
