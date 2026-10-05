"""Unit test — real in-memory SQLite for the keyword-search path (same pattern
`test_discount_tools_currency.py` already uses), per Architecture.md's tests/unit/ scope.

`recall` (2026-10-05, tiered conversation memory) — the on-demand tool replacing the old
unconditional semantic-memory call every turn used to make. Verifies: a keyword hit is found and
returned without ever reaching the (expensive) semantic fallback; missing session_id/query both
degrade honestly instead of erroring."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.chat_turn import ChatTurnModel
from src.models.session import SessionModel  # noqa: F401 — registers `sessions` for the FK
from src.services.tools.recall import RecallTool


@pytest.mark.asyncio
async def test_recall_finds_a_keyword_match_without_falling_back_to_semantic(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        db.add(ChatTurnModel(
            id="t1", session_id="s1",
            user_text="Make the discount 15% on the Nothing Phone",
            assistant_text="Applied 15% discount.",
        ))
        await db.commit()

    monkeypatch.setattr("src.services.tools.recall.async_session_factory", session_factory)

    # The expensive path is the actual semantic QUERY call, not constructing the service (its
    # `__init__` eagerly builds a knowledge-provider client regardless) — assert that call never
    # fires, since a real keyword hit was found first.
    with patch(
        "src.services.knowledge.chat_memory_service.ChatMemoryService.get_relevant_history",
        new=AsyncMock(),
    ) as mock_semantic:
        tool = RecallTool()
        result = await tool.run({"query": "discount"}, context={"session_id": "s1"})

    assert result.ok is True
    assert result.data["source"] == "keyword"
    assert any("15%" in t["user"] for t in result.data["turns"])
    mock_semantic.assert_not_called()


@pytest.mark.asyncio
async def test_recall_falls_back_to_semantic_when_keyword_search_is_empty(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("src.services.tools.recall.async_session_factory", session_factory)

    with patch(
        "src.services.knowledge.chat_memory_service.ChatMemoryService.get_relevant_history",
        new=AsyncMock(return_value="semantic answer"),
    ):
        tool = RecallTool()
        result = await tool.run({"query": "something nobody said"}, context={"session_id": "s1"})

    assert result.ok is True
    assert result.data["source"] == "semantic"
    assert result.data["summary"] == "semantic answer"


@pytest.mark.asyncio
async def test_recall_without_session_id_degrades_honestly_not_an_error():
    tool = RecallTool()
    result = await tool.run({"query": "anything"}, context=None)
    assert result.ok is True
    assert result.data["turns"] == []


@pytest.mark.asyncio
async def test_recall_without_query_or_artifact_id_degrades_honestly():
    tool = RecallTool()
    result = await tool.run({}, context={"session_id": "s1"})
    assert result.ok is True
    assert result.data["turns"] == []
