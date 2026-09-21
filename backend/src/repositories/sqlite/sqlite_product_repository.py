from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.product_profile import ProductProfileModel


class SqliteProductRepository:
    """Satisfies the ProductRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, product: ProductProfileModel) -> ProductProfileModel:
        self._db.add(product)
        await self._db.commit()
        await self._db.refresh(product)
        return product

    async def get(self, product_id: str) -> ProductProfileModel | None:
        result = await self._db.execute(
            select(ProductProfileModel).where(ProductProfileModel.id == product_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ProductProfileModel]:
        result = await self._db.execute(select(ProductProfileModel))
        return list(result.scalars().all())
