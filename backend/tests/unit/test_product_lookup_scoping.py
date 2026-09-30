"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found bug (2026-09-30): `product_lookup` did an unscoped semantic search over a user's
WHOLE product collection, guaranteed to surface whichever product a fact happened to be indexed
under, not necessarily the one relevant to the current turn. Verifies: with a `product_id` in
context, the tool calls the new `query_document` (scoped); without one, it's byte-identical to the
prior unscoped `query()` behavior (regression guard).
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.tools.product_lookup import ProductLookupTool


@pytest.mark.asyncio
async def test_scopes_to_resolved_product_when_context_has_one():
    tool = ProductLookupTool()
    with patch("src.services.tools.product_lookup.get_knowledge_provider") as mock_knowledge:
        mock_knowledge.return_value.query_document = AsyncMock(return_value="4a-only facts")
        mock_knowledge.return_value.query = AsyncMock(return_value="should not be called")

        result = await tool.run(
            {"question": "must-show facts"},
            context={"user_id": "u1", "product_id": "p-4a"},
        )

        assert result.data["facts"] == "4a-only facts"
        mock_knowledge.return_value.query_document.assert_awaited_once_with(
            collection="product_u1", doc_id="p-4a", question="must-show facts",
        )
        mock_knowledge.return_value.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_unscoped_when_no_product_id_resolved_regression_guard():
    tool = ProductLookupTool()
    with patch("src.services.tools.product_lookup.get_knowledge_provider") as mock_knowledge:
        mock_knowledge.return_value.query = AsyncMock(return_value="whole-collection facts")
        mock_knowledge.return_value.query_document = AsyncMock(return_value="should not be called")

        result = await tool.run({"question": "must-show facts"}, context={"user_id": "u1"})

        assert result.data["facts"] == "whole-collection facts"
        mock_knowledge.return_value.query.assert_awaited_once_with(
            collection="product_u1", question="must-show facts",
        )
        mock_knowledge.return_value.query_document.assert_not_awaited()
