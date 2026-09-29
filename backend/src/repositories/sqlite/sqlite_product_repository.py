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

    async def list_for_user(self, user_id: str) -> list[ProductProfileModel]:
        result = await self._db.execute(
            select(ProductProfileModel).where(ProductProfileModel.user_id == user_id)
        )
        return list(result.scalars().all())

    async def update(self, product: ProductProfileModel) -> ProductProfileModel:
        return await self.add(product)

    async def delete(self, product_id: str) -> None:
        product = await self.get(product_id)
        if product is None:
            return
        await self._db.delete(product)
        await self._db.commit()
