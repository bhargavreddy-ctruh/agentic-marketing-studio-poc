from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.tool_call_log import ToolCallLogModel


class PostgresToolCallLogRepository:
    """Satisfies the ToolCallLogRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, log: ToolCallLogModel) -> ToolCallLogModel:
        self._db.add(log)
        await self._db.commit()
        await self._db.refresh(log)
        return log

    async def list_for_session(self, session_id: str) -> list[ToolCallLogModel]:
        result = await self._db.execute(
            select(ToolCallLogModel)
            .where(ToolCallLogModel.session_id == session_id)
            .order_by(ToolCallLogModel.created_at)
        )
        return list(result.scalars().all())
