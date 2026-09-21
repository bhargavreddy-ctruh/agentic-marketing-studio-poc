from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.generation_job import GenerationJobModel


class SqliteGenerationJobRepository:
    """Satisfies the GenerationJobRepository protocol (repositories/base.py)."""

    def __init__(self, session: AsyncSession):
        self._db = session

    async def add(self, job: GenerationJobModel) -> GenerationJobModel:
        self._db.add(job)
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def get(self, job_id: str) -> GenerationJobModel | None:
        result = await self._db.execute(
            select(GenerationJobModel).where(GenerationJobModel.id == job_id)
        )
        return result.scalar_one_or_none()

    async def update(self, job: GenerationJobModel) -> GenerationJobModel:
        await self._db.commit()
        await self._db.refresh(job)
        return job
