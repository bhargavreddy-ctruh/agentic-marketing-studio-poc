"""Integration test — real SQLite, per Architecture.md's tests/integration/ scope.

Fidelity audit (2026-10-05): `product_lookup`'s own docstring promises "exact facts... rather than
letting a model free-generate them" (the guardrail-first pattern), but it only ever returned a
RAG-summarized free-text answer — lossy by construction, and a real, live-found cause of generated
ads missing facts that genuinely existed (the vector-retrieval question/answer pass can miss or
paraphrase a specific field). Verifies: when `product_id` resolves to a real row, the tool now
returns the REAL structured `attributes` dict verbatim, not just a RAG paraphrase."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.product_profile import ProductProfileModel
from src.repositories.postgres.postgres_product_repository import PostgresProductRepository
from src.services.tools.product_lookup import ProductLookupTool


@pytest.mark.asyncio
async def test_returns_real_structured_attributes_not_just_rag_summary(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4().hex

    real_attributes = {
        "summary": "A smartwatch", "price": 164900, "currency": "₹", "discount_percent": 16,
        "must_show": ["AMOLED display", "7-day battery"], "never_show": ["waterproof"],
        "claims_allowed": ["longest battery in its class"], "claims_disallowed": [], "color": "black",
    }
    async with session_factory() as db:
        product = await PostgresProductRepository(db).add(ProductProfileModel(
            id=uuid.uuid4().hex, user_id=user_id, name="Galaxy Watch", attributes=real_attributes,
        ))

    monkeypatch.setattr("src.services.tools.product_lookup.async_session_factory", session_factory)

    tool = ProductLookupTool()
    with patch("src.services.tools.product_lookup.get_knowledge_provider") as mock_knowledge:
        # Even a lossy/partial RAG answer must not hide the real structured attributes.
        mock_knowledge.return_value.query_document = AsyncMock(return_value="a smartwatch")
        result = await tool.run(
            {"question": "what must the ad show?"}, context={"user_id": user_id, "product_id": product.id},
        )

    assert result.ok is True
    assert result.data["configured"] is True
    assert result.data["attributes"] == real_attributes
    assert "AMOLED display" in result.data["attributes"]["must_show"]
    assert result.data["attributes"]["currency"] == "₹"
