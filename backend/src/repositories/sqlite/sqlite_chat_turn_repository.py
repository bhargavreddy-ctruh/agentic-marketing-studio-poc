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

    async def list_for_session(
        self, session_id: str, *, limit: int | None = None, before_id: str | None = None
    ) -> list[ChatTurnModel]:
        """`limit`/`before_id` (2026-10-05, chat lazy-load) — the chat panel's initial render only
        needs the most recent handful of turns; every other caller (brief/memory building) still
        gets the full, unbounded, ascending list by leaving both unset, unchanged from before.

        `before_id` is a cursor, not an offset — paging by `created_at` directly avoids the classic
        "page 2 skips/repeats a row" bug an offset gets when a new turn is added between two page
        fetches (irrelevant here since we only page backwards into history that can't change, but
        the cursor is also just as cheap and one less thing to get wrong)."""
        query = select(ChatTurnModel).where(ChatTurnModel.session_id == session_id)
        if before_id is not None:
            cursor = await self._db.execute(
                select(ChatTurnModel.created_at).where(ChatTurnModel.id == before_id)
            )
            cursor_created_at = cursor.scalar_one_or_none()
            if cursor_created_at is not None:
                query = query.where(ChatTurnModel.created_at < cursor_created_at)
        if limit is None:
            query = query.order_by(ChatTurnModel.created_at)
            result = await self._db.execute(query)
            return list(result.scalars().all())
        # Newest-first + limit, then reverse back to the ascending order every caller expects —
        # the only way to get "the most recent N" out of a LIMIT clause.
        query = query.order_by(ChatTurnModel.created_at.desc()).limit(limit)
        result = await self._db.execute(query)
        turns = list(result.scalars().all())
        turns.reverse()
        return turns
