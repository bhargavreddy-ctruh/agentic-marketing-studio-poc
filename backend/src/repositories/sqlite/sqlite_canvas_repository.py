from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.canvas_element import CanvasElementModel


class SqliteCanvasRepository:
    """Satisfies the CanvasRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add_element(self, element: CanvasElementModel) -> CanvasElementModel:
        self._db.add(element)
        await self._db.commit()
        await self._db.refresh(element)
        return element

    async def get_element(self, element_id: str) -> CanvasElementModel | None:
        result = await self._db.execute(
            select(CanvasElementModel).where(CanvasElementModel.id == element_id)
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: str) -> list[CanvasElementModel]:
        result = await self._db.execute(
            select(CanvasElementModel)
            .where(CanvasElementModel.session_id == session_id)
            .order_by(CanvasElementModel.created_at)
        )
        return list(result.scalars().all())

    async def update_element(self, element: CanvasElementModel) -> CanvasElementModel:
        await self._db.commit()
        await self._db.refresh(element)
        return element

    async def update_compliance_status(self, element_id: str, status: str) -> None:
        """Update just the compliance status of an element."""
        from sqlalchemy import update
        await self._db.execute(
            update(CanvasElementModel)
            .where(CanvasElementModel.id == element_id)
            .values(compliance_status=status)
        )
        await self._db.commit()
