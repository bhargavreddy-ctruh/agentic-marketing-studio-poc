from __future__ import annotations

from ..core.local_storage import load_asset, public_url
from ..models.canvas_element import CanvasElementModel
from ..schemas.canvas.responses import CanvasElementResponse, CanvasStateResponse


def _text_content(entity: CanvasElementModel) -> str | None:
    """A real, live-found bug (2026-09-22): a `text_card_writer` tool call (the real, modular
    path — `composition_artist`/`shot_planner`/`lighting_designer`/`narrator`) writes its content
    to a real `text/plain` ASSET on disk via `save_asset`, referenced by `storage_ref` — it never
    duplicates the text into `metadata_json["text"]` at all (only the older, honest-fallback path
    for when a specialist skips the tool call does that). This mapper only ever read
    `metadata_json.get("text")`, so every element produced by the REAL tool call rendered as an
    empty card on the canvas — the text existed, correctly, on disk, the API response just never
    surfaced it. Falls back to the asset file's real content when `metadata_json` has none;
    genuinely no content anywhere (should not happen) returns None, not a fabricated empty string."""
    embedded = (entity.metadata_json or {}).get("text")
    if embedded:
        return embedded
    if entity.element_type == "text" and entity.storage_ref:
        loaded = load_asset(entity.storage_ref)
        if loaded is not None:
            data, mime_type = loaded
            if mime_type == "text/plain":
                return data.decode("utf-8", errors="replace")
    return None


# Priority order — the field most likely to be what a human would actually call this element,
# checked per element type's own real specialist output (illustrator/camera_director/sound_designer
# /reference_curator/lighting_designer's own result fields — see visual_design_lead.py/motion_lead.py
# for where each is set).
_DESCRIPTION_FIELDS = (
    "image_prompt", "motion_prompt", "voiceover_line", "primary_shot",
    "aesthetic_direction", "environment_description",
)


def _description(entity: CanvasElementModel) -> str | None:
    """A real, short, honest label for what this element actually IS (2026-09-22, per an explicit
    user ask: "label everything properly and relative to what's generated") — canvas tiles and the
    Elements panel previously showed only a generic `element_type` ("image"/"video") with no
    indication of real content, making a long session's many generated elements indistinguishable
    at a glance (a real, live-found symptom: a user unable to tell which of several "image" tiles
    was the Ferrari vs. the logo vs. the green car without opening each one). Pulled from whichever
    of the element's own real recorded metadata fields actually has content, in priority order —
    never fabricated, never guessed; returns None only if genuinely nothing was ever recorded."""
    meta = entity.metadata_json or {}
    for field in _DESCRIPTION_FIELDS:
        value = meta.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    label = meta.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip().replace("_", " ")
    return None


class CanvasMapper:
    @staticmethod
    def to_response(entity: CanvasElementModel) -> CanvasElementResponse:
        return CanvasElementResponse(
            id=entity.id,
            session_id=entity.session_id,
            element_type=entity.element_type,
            produced_by_specialist=entity.produced_by_specialist,
            version=entity.version,
            storage_ref=entity.storage_ref,
            url=public_url(entity.storage_ref),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            pending_storage_ref=entity.pending_storage_ref,
            pending_action=entity.pending_action,
            last_comment=(entity.metadata_json or {}).get("comment"),
            compliance_status=entity.compliance_status,
            text_content=_text_content(entity),
            description=_description(entity),
            alignment_warning=(entity.metadata_json or {}).get("alignment_warning"),
            product_id=entity.product_id,
            product_name=entity.product_name,
            parent_element_id=entity.parent_element_id,
        )

    @staticmethod
    def to_state_response(
        session_id: str, entities: list[CanvasElementModel]
    ) -> CanvasStateResponse:
        return CanvasStateResponse(
            session_id=session_id,
            elements=[CanvasMapper.to_response(e) for e in entities],
        )
