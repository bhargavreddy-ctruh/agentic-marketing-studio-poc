"""Entity <-> DTO for mood board assets. Rules.md: never return an entity/model object from a service."""
from __future__ import annotations

from ..models.mood_board_asset import MoodBoardAssetModel
from ..schemas.mood_board.responses import MoodBoardAssetResponse


class MoodBoardMapper:
    @staticmethod
    def to_response(entity: MoodBoardAssetModel) -> MoodBoardAssetResponse:
        return MoodBoardAssetResponse(
            id=entity.id,
            storage_ref=entity.storage_ref,
            mime_type=entity.mime_type,
            description=entity.description,
            created_at=entity.created_at,
        )
