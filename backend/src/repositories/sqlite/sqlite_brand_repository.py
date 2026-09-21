from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.brand_profile import BrandProfileModel


class SqliteBrandRepository:
    """Satisfies the BrandRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, brand: BrandProfileModel) -> BrandProfileModel:
        self._db.add(brand)
        await self._db.commit()
        await self._db.refresh(brand)
        return brand

    async def get(self, brand_id: str) -> BrandProfileModel | None:
        result = await self._db.execute(
            select(BrandProfileModel).where(BrandProfileModel.id == brand_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[BrandProfileModel]:
        result = await self._db.execute(select(BrandProfileModel))
        return list(result.scalars().all())
