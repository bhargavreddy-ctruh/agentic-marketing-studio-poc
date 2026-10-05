from __future__ import annotations

import asyncio

import httpx

from ..core.local_storage import get_metadata_batch
from ..models.asset_metadata import AssetMetadataModel
from ..models.canvas_element import CanvasElementModel
from ..schemas.canvas.responses import CanvasElementResponse, CanvasStateResponse


async def _text_content(
    entity: CanvasElementModel, meta: AssetMetadataModel | None
) -> str | None:
    """A real, live-found bug (2026-09-22): a `text_card_writer` tool call (the real, modular
    path — `composition_artist`/`shot_planner`/`lighting_designer`/`narrator`) writes its content
    to a real `text/plain` ASSET via `save_asset`, referenced by `storage_ref` — it never
    duplicates the text into `metadata_json["text"]` at all (only the older, honest-fallback path
    for when a specialist skips the tool call does that). This mapper only ever read
    `metadata_json.get("text")`, so every element produced by the REAL tool call rendered as an
    empty card on the canvas — the text existed, correctly, the API response just never surfaced
    it. Falls back to the asset's real content (`meta`, already batch-fetched by the caller — no
    DB call here, only a Cloudinary bytes fetch for the rare genuine text-asset case) when
    `metadata_json` has none; genuinely no content anywhere (should not happen) returns None, not
    a fabricated empty string."""
    embedded = (entity.metadata_json or {}).get("text")
    if embedded:
        return embedded
    if entity.element_type == "text" and meta is not None and meta.cloudinary_url and meta.mime_type == "text/plain":
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(meta.cloudinary_url)
        if response.status_code == 200:
            return response.content.decode("utf-8", errors="replace")
    return None


# Priority order — the field most likely to be what a human would actually call this element,
# checked per element type's own real specialist output (illustrator/camera_director/sound_designer
# /reference_curator/lighting_designer's own result fields — see visual_design_lead.py/motion_lead.py
# for where each is set).
#
# `verified_description` (2026-09-30) checked FIRST, above every generation prompt — a real vision
# call's account of what's ACTUALLY in the image (`core/element_context.py`'s
# `get_verified_image_description`) is more trustworthy than a generation prompt, which describes
# what was ASKED for, not necessarily what the model actually delivered.
_DESCRIPTION_FIELDS = (
    "verified_description",
    "image_prompt", "motion_prompt", "voiceover_line", "primary_shot",
    "aesthetic_direction", "environment_description",
)


# Real, live-found performance bug (2026-10-05, fidelity audit): `verified_description` is a
# vision-model essay (`session_service.py`'s `_describe_uploaded_image` asks for "detail", capped
# at 700 tokens) — several hundred words per image element, sent in FULL on every single canvas
# list fetch for every image element. Server-side LLM grounding reads the full, untruncated text
# directly from `metadata_json`/`core/element_context.py` (bypassing this mapper entirely) — this
# cap only shrinks the hot, frequently-polled HTTP list response, it changes nothing about what the
# backend itself uses for grounding.
_LIST_DESCRIPTION_MAX_CHARS = 300


def _description(entity: CanvasElementModel, *, truncate: bool = False) -> str | None:
    """A real, short, honest label for what this element actually IS (2026-09-22, per an explicit
    user ask: "label everything properly and relative to what's generated") — canvas tiles and the
    Elements panel previously showed only a generic `element_type` ("image"/"video") with no
    indication of real content, making a long session's many generated elements indistinguishable
    at a glance (a real, live-found symptom: a user unable to tell which of several "image" tiles
    was the Ferrari vs. the logo vs. the green car without opening each one). Pulled from whichever
    of the element's own real recorded metadata fields actually has content, in priority order —
    never fabricated, never guessed; returns None only if genuinely nothing was ever recorded."""
    meta = entity.metadata_json or {}
    value: str | None = None
    for field in _DESCRIPTION_FIELDS:
        candidate = meta.get(field)
        if isinstance(candidate, str) and candidate.strip():
            value = candidate.strip()
            break
    if value is None:
        label = meta.get("label")
        if isinstance(label, str) and label.strip():
            value = label.strip().replace("_", " ")
    if value is not None and truncate and len(value) > _LIST_DESCRIPTION_MAX_CHARS:
        value = value[:_LIST_DESCRIPTION_MAX_CHARS].rstrip() + "…"
    return value


class CanvasMapper:
    @staticmethod
    async def _build_response(
        entity: CanvasElementModel, meta_by_ref: dict[str, AssetMetadataModel], *, truncate_description: bool = False
    ) -> CanvasElementResponse:
        meta = meta_by_ref.get(entity.storage_ref) if entity.storage_ref else None
        return CanvasElementResponse(
            id=entity.id,
            session_id=entity.session_id,
            element_type=entity.element_type,
            produced_by_specialist=entity.produced_by_specialist,
            version=entity.version,
            storage_ref=entity.storage_ref,
            url=meta.cloudinary_url if meta else None,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            pending_storage_ref=entity.pending_storage_ref,
            pending_action=entity.pending_action,
            last_comment=(entity.metadata_json or {}).get("comment"),
            compliance_status=entity.compliance_status,
            text_content=await _text_content(entity, meta),
            description=_description(entity, truncate=truncate_description),
            alignment_warning=(entity.metadata_json or {}).get("alignment_warning"),
            product_id=entity.product_id,
            product_name=entity.product_name,
            parent_element_id=entity.parent_element_id,
        )

    @staticmethod
    async def to_response(entity: CanvasElementModel) -> CanvasElementResponse:
        meta_by_ref = await get_metadata_batch([entity.storage_ref] if entity.storage_ref else [])
        return await CanvasMapper._build_response(entity, meta_by_ref)

    @staticmethod
    async def to_state_response(
        session_id: str, entities: list[CanvasElementModel]
    ) -> CanvasStateResponse:
        # Real, live-found incident (2026-09-30): this used to call `to_response()` per element via
        # `asyncio.gather` — each one opening its OWN DB session, so a canvas with more elements
        # than Supabase's Session Pooler `pool_size` (15) genuinely exhausted it live
        # ("max clients reached in session mode"). One batched query up front (`get_metadata_batch`)
        # instead — `asyncio.gather` below is now safe: building each response from the shared
        # dict involves no further DB connections at all, only an occasional Cloudinary bytes
        # fetch (httpx, not a DB session) for the rare genuine text-asset element.
        #
        # `truncate_description=True` (2026-10-05, fidelity audit): this is the hot, frequently-
        # polled list endpoint (every canvas load, every 4s DNA-tab poll, every compliance-status
        # poll) — a multi-hundred-word vision-model essay per image element, repeated on every one
        # of those fetches, was real, measured payload bloat. `to_response` (single-element fetch)
        # keeps the full text; nothing server-side reads descriptions through this mapper anyway.
        refs = [e.storage_ref for e in entities if e.storage_ref]
        meta_by_ref = await get_metadata_batch(refs)
        elements = await asyncio.gather(
            *(CanvasMapper._build_response(e, meta_by_ref, truncate_description=True) for e in entities)
        )
        return CanvasStateResponse(session_id=session_id, elements=list(elements))
