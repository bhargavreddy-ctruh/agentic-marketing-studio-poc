from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.mood_board_asset import MoodBoardAssetModel


class SqliteMoodBoardRepository:
    """Satisfies the MoodBoardRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, asset: MoodBoardAssetModel) -> MoodBoardAssetModel:
        self._db.add(asset)
        await self._db.commit()
        await self._db.refresh(asset)
        return asset

    async def get(self, asset_id: str) -> MoodBoardAssetModel | None:
        result = await self._db.execute(
            select(MoodBoardAssetModel).where(MoodBoardAssetModel.id == asset_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[MoodBoardAssetModel]:
        result = await self._db.execute(select(MoodBoardAssetModel))
        return list(result.scalars().all())
