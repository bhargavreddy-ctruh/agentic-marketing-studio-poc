from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CanvasElementResponse(BaseModel):
    id: str
    session_id: str
    element_type: str
    produced_by_specialist: str
    version: int
    storage_ref: str | None
    created_at: datetime
    updated_at: datetime
    # A real staged edit awaiting approve/reject (Memory.md, Phase 4 "approve" mode) — all null
    # in "auto" mode, where edits still apply immediately as before.
    pending_storage_ref: str | None = None
    pending_action: str | None = None
    # Real, already-stored data (comment_service.py writes this on every comment resolution) —
    # surfaced here so a client can show a comment indicator without needing metadata_json's full
    # internal shape exposed over the API (Rules.md: layers talk through typed DTOs, not raw dicts).
    last_comment: str | None = None
    # The real compliance gate's last verdict for this element's current version — null until the
    # gate has actually run once (compliance_gate.py, now invoked automatically right after
    # generation — session_service.py). False renders as a real UI indicator, not swallowed.
    compliance_passed: bool | None = None


class CanvasStateResponse(BaseModel):
    session_id: str
    elements: list[CanvasElementResponse]


class CanvasElementVersionResponse(BaseModel):
    version: int
    storage_ref: str
    created_at: datetime


class AssetUploadResponse(BaseModel):
    storage_ref: str
    mime_type: str
