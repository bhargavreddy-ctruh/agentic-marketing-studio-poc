from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import UserModel


class SqliteUserRepository:
    """Satisfies the UserRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, user: UserModel) -> UserModel:
        self._db.add(user)
        await self._db.commit()
        await self._db.refresh(user)
        return user

    async def get_by_id(self, user_id: str) -> UserModel | None:
        result = await self._db.execute(select(UserModel).where(UserModel.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> UserModel | None:
        result = await self._db.execute(select(UserModel).where(UserModel.username == username))
        return result.scalar_one_or_none()
