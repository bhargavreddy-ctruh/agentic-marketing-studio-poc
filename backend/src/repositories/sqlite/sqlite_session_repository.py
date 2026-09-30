from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.canvas_element import CanvasElementModel
from ...models.canvas_element_version import CanvasElementVersionModel
from ...models.chat_turn import ChatTurnModel
from ...models.generation_job import GenerationJobModel
from ...models.session import SessionModel
from ...models.tool_call_log import ToolCallLogModel


class SqliteSessionRepository:
    """Satisfies the SessionRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, session: SessionModel) -> SessionModel:
        self._db.add(session)
        await self._db.commit()
        await self._db.refresh(session)
        return session

    async def get(self, session_id: str) -> SessionModel | None:
        result = await self._db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        return result.scalar_one_or_none()

    async def update(self, session: SessionModel) -> SessionModel:
        await self._db.commit()
        await self._db.refresh(session)
        return session

    async def list_for_user(self, user_id: str) -> list[SessionModel]:
        result = await self._db.execute(
            select(SessionModel)
            .where(SessionModel.user_id == user_id)
            .order_by(SessionModel.updated_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, session_id: str) -> None:
        """Real, live-found gap (2026-09-30, explicit user ask: "add a delete/edit button on
        workflows") — no FK on any dependent table (`canvas_elements`, `chat_turns`,
        `generation_jobs`, `tool_call_logs`, `canvas_element_versions`) declares
        `ondelete="CASCADE"` (models/*.py), so a plain `DELETE FROM sessions` would fail against
        Postgres with a foreign-key violation the moment a session has any real content. Deletes
        every dependent row first, in dependency order (element VERSIONS before the elements they
        version), all within this same repository call's transaction — one real, all-or-nothing
        cascade, never a partially-deleted workflow left behind by a mid-way failure."""
        element_ids_subquery = select(CanvasElementModel.id).where(
            CanvasElementModel.session_id == session_id
        )
        await self._db.execute(
            delete(CanvasElementVersionModel).where(
                CanvasElementVersionModel.element_id.in_(element_ids_subquery)
            )
        )
        await self._db.execute(delete(CanvasElementModel).where(CanvasElementModel.session_id == session_id))
        await self._db.execute(delete(ChatTurnModel).where(ChatTurnModel.session_id == session_id))
        await self._db.execute(delete(GenerationJobModel).where(GenerationJobModel.session_id == session_id))
        await self._db.execute(delete(ToolCallLogModel).where(ToolCallLogModel.session_id == session_id))
        await self._db.execute(delete(SessionModel).where(SessionModel.id == session_id))
        await self._db.commit()
