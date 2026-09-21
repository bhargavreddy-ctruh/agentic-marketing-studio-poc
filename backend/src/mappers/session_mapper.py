"""Entity <-> DTO for sessions. Rules.md: never return an entity/model object from a service."""
from __future__ import annotations

from ..models.session import SessionModel
from ..schemas.sessions.responses import IdeationPrompt, SessionResponse


class SessionMapper:
    @staticmethod
    def to_response(
        entity: SessionModel, *, next_prompt: IdeationPrompt | None = None
    ) -> SessionResponse:
        return SessionResponse(
            id=entity.id,
            status=entity.status,
            approval_mode=entity.approval_mode,
            brief=entity.brief,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            next_prompt=next_prompt,
        )
