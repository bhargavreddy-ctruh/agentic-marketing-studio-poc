"""Integration test — real SQLite, per Architecture.md's tests/integration/ scope.

Covers the chat lazy-load pagination added to `SqliteChatTurnRepository.list_for_session`
(2026-10-05): `limit` alone returns the most recent N turns (oldest-first), and `before_id` pages
further back in history from an already-loaded turn's id.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.chat_turn import ChatTurnModel
from src.models.session import SessionModel  # noqa: F401 — registers `sessions` for the FK below
from src.repositories.sqlite.sqlite_chat_turn_repository import SqliteChatTurnRepository


async def _make_repo(db):
    return SqliteChatTurnRepository(db)


def _turn(session_id: str, index: int, created_at: datetime) -> ChatTurnModel:
    return ChatTurnModel(
        id=f"turn-{index}",
        session_id=session_id,
        user_text=f"message {index}",
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_limit_alone_returns_most_recent_n_oldest_first():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        repo = await _make_repo(db)
        session_id = uuid.uuid4().hex
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            db.add(_turn(session_id, i, base + timedelta(seconds=i)))
        await db.commit()

        turns = await repo.list_for_session(session_id, limit=2)

        assert [t.id for t in turns] == ["turn-3", "turn-4"]


@pytest.mark.asyncio
async def test_before_id_pages_further_back_in_history():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        repo = await _make_repo(db)
        session_id = uuid.uuid4().hex
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            db.add(_turn(session_id, i, base + timedelta(seconds=i)))
        await db.commit()

        latest = await repo.list_for_session(session_id, limit=2)
        assert [t.id for t in latest] == ["turn-3", "turn-4"]

        older = await repo.list_for_session(session_id, limit=2, before_id=latest[0].id)
        assert [t.id for t in older] == ["turn-1", "turn-2"]

        oldest = await repo.list_for_session(session_id, limit=2, before_id=older[0].id)
        assert [t.id for t in oldest] == ["turn-0"]


@pytest.mark.asyncio
async def test_no_limit_returns_full_ascending_history_unchanged():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        repo = await _make_repo(db)
        session_id = uuid.uuid4().hex
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            db.add(_turn(session_id, i, base + timedelta(seconds=i)))
        await db.commit()

        turns = await repo.list_for_session(session_id)

        assert [t.id for t in turns] == ["turn-0", "turn-1", "turn-2"]
