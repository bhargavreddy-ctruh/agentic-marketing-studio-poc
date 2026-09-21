from __future__ import annotations

from ..models.canvas_element import CanvasElementModel
from ..schemas.canvas.responses import CanvasElementResponse, CanvasStateResponse


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
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            pending_storage_ref=entity.pending_storage_ref,
            pending_action=entity.pending_action,
            last_comment=(entity.metadata_json or {}).get("comment"),
            compliance_status=entity.compliance_status,
        )

    @staticmethod
    def to_state_response(
        session_id: str, entities: list[CanvasElementModel]
    ) -> CanvasStateResponse:
        return CanvasStateResponse(
            session_id=session_id,
            elements=[CanvasMapper.to_response(e) for e in entities],
        )
