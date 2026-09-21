"""Integration test — real SQLite, per Architecture.md's tests/integration/ scope."""
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.session import SessionModel
from src.repositories.sqlite.sqlite_session_repository import SqliteSessionRepository


@pytest.mark.asyncio
async def test_session_round_trips_through_repository():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        repo = SqliteSessionRepository(db)
        created = await repo.add(SessionModel(id=uuid.uuid4().hex, status="ideating", brief={}))

        fetched = await repo.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.status == "ideating"

        missing = await repo.get("does-not-exist")
        assert missing is None
