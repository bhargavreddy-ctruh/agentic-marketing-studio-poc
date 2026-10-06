from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.app_setting import AppSettingModel


class PostgresAppSettingRepository:
    """Satisfies the AppSettingRepository protocol (repositories/base.py). Plain SQLAlchemy
    Core/ORM — works against Postgres too despite this module's name, same as every other
    repository in this app since the Supabase migration."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def list_all(self) -> list[AppSettingModel]:
        result = await self._db.execute(select(AppSettingModel))
        return list(result.scalars().all())
