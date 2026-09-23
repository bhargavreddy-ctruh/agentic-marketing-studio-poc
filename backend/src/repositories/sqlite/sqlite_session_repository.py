from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.session import SessionModel


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
