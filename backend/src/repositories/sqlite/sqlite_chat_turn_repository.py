from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.chat_turn import ChatTurnModel


class SqliteChatTurnRepository:
    """Satisfies the ChatTurnRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, turn: ChatTurnModel) -> ChatTurnModel:
        self._db.add(turn)
        await self._db.commit()
        await self._db.refresh(turn)
        return turn

    async def list_for_session(self, session_id: str) -> list[ChatTurnModel]:
        result = await self._db.execute(
            select(ChatTurnModel)
            .where(ChatTurnModel.session_id == session_id)
            .order_by(ChatTurnModel.created_at)
        )
        return list(result.scalars().all())
