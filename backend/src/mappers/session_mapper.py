"""Entity <-> DTO for sessions. Rules.md: never return an entity/model object from a service."""
from __future__ import annotations

from ..models.session import SessionModel
from ..schemas.sessions.responses import IdeationPrompt, SessionResponse


class SessionMapper:
    @staticmethod
    def to_response(entity: SessionModel) -> SessionResponse:
        # Reconstructed from the persisted column, not recomputed — so a plain GET (a page refresh,
        # the "refresh" button) reproduces exactly what the last real turn actually returned,
        # instead of always coming back null (a real, disclosed gap until now: see the field's own
        # docstring in models/session.py).
        next_prompt = (
            IdeationPrompt(**entity.next_prompt_json) if entity.next_prompt_json else None
        )
        return SessionResponse(
            id=entity.id,
            title=entity.title,
            status=entity.status,
            approval_mode=entity.approval_mode,
            brief=entity.brief,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            next_prompt=next_prompt,
        )
