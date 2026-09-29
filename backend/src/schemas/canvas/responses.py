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
    # Real, live-found latency win (2026-09-29, per an explicit user ask to "reduce latency"):
    # when the asset already lives on Cloudinary, this is its real, direct CDN url — the frontend
    # uses it as an <img src> straight to Cloudinary instead of proxying bytes a second time
    # through this backend's own `/canvas/assets/{storage_ref}` route (`core/local_storage.py`'s
    # `public_url()`). None in local-disk dev mode (no CLOUDINARY_URL configured) or when this
    # element has no asset yet — callers fall back to the proxy route in both cases, unchanged.
    url: str | None = None
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
    # The real compliance gate's status for this element's current version: "running" | "passed" |
    # "failed" (compliance_gate.py, kicked off as a real background task right after generation —
    # session_service.py). "running" is the honest default the instant an element is created.
    compliance_status: str = "running"
    # A real, short, honest label for what this element actually IS (2026-09-22, per an explicit
    # user ask: "label everything properly and relative to what's generated") — pulled from
    # whichever of the element's own real recorded metadata fields actually describes it
    # (image_prompt/motion_prompt/voiceover_line/etc, see `canvas_mapper.py`'s own docstring),
    # never fabricated. Same "curated DTO field, not the raw metadata dict" pattern `last_comment`/
    # `text_content` already use.
    description: str | None = None
    # Real, already-generated text (a shot list, a scene description, a creative brief — the same
    # kind of card the reference product shows on its own canvas) for `element_type: "text"`
    # elements, which have no `storage_ref` at all (there's no binary asset — the text itself IS
    # the content). Surfaced the same way `last_comment` already is: a real already-stored field,
    # not metadata_json's full internal shape (Rules.md: layers talk through typed DTOs).
    text_content: str | None = None
    alignment_warning: str | None = None
    # Canvas Grouping (2026-09-25, revised: grouped by real Product DNA, not a free-text campaign
    # — a workflow IS one campaign) — which real product this element belongs to, and which
    # element (if any) it was generated from.
    product_id: str | None = None
    product_name: str | None = None
    parent_element_id: str | None = None


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
