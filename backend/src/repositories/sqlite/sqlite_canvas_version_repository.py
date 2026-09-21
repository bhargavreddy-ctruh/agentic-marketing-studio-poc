from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.canvas_element_version import CanvasElementVersionModel


class SqliteCanvasVersionRepository:
    """Satisfies the CanvasVersionRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, version: CanvasElementVersionModel) -> CanvasElementVersionModel:
        self._db.add(version)
        await self._db.commit()
        await self._db.refresh(version)
        return version

    async def list_for_element(self, element_id: str) -> list[CanvasElementVersionModel]:
        result = await self._db.execute(
            select(CanvasElementVersionModel)
            .where(CanvasElementVersionModel.element_id == element_id)
            .order_by(CanvasElementVersionModel.version)
        )
        return list(result.scalars().all())

    async def delete_versions_after(self, element_id: str, version: int) -> None:
        """Discards any 'future' versions left behind by an earlier undo — standard
        undo-then-edit semantics: a genuinely new edit made while not at the latest version
        abandons the redo history beyond that point, rather than branching it."""
        await self._db.execute(
            delete(CanvasElementVersionModel).where(
                CanvasElementVersionModel.element_id == element_id,
                CanvasElementVersionModel.version > version,
            )
        )
        await self._db.commit()
