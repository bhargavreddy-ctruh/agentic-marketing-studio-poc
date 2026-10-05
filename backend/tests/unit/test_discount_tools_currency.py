"""Unit test — no DB for discount_math_calculator; real in-memory SQLite for
discount_claims_calculator, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05): both discount tools used to hardcode "$" into the overlay text they
hand to composition_artist/overlay_artist, regardless of the product's real currency — a real
scraped ₹-denominated product would get a "$"-prefixed price baked straight onto the generated ad.
Verifies both tools now use the real/given currency, with no symbol when none is actually known."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.product_profile import ProductProfileModel
from src.repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from src.services.tools.discount_claims_calculator import DiscountClaimsCalculatorTool
from src.services.tools.discount_math_calculator import DiscountMathCalculatorTool


@pytest.mark.asyncio
async def test_discount_math_calculator_uses_given_currency_not_hardcoded_dollar(monkeypatch):
    tool = DiscountMathCalculatorTool()
    result = await tool.run({"base_price": 35000, "discount_percent": 15, "currency": "₹"})
    assert result.ok is True
    assert "₹" in result.data["overlay_text_should_use"]
    assert "$" not in result.data["overlay_text_should_use"]


@pytest.mark.asyncio
async def test_discount_math_calculator_omits_symbol_when_none_given():
    tool = DiscountMathCalculatorTool()
    result = await tool.run({"base_price": 100, "discount_percent": 10})
    assert result.ok is True
    assert "$" not in result.data["overlay_text_should_use"]


@pytest.mark.asyncio
async def test_discount_claims_calculator_uses_products_real_currency(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        product = await SqliteProductRepository(db).add(ProductProfileModel(
            id=uuid.uuid4().hex, user_id=uuid.uuid4().hex, name="Test Watch",
            attributes={"price": 164900, "discount_percent": 16, "currency": "₹"},
        ))

    monkeypatch.setattr(
        "src.services.tools.discount_claims_calculator.async_session_factory", session_factory,
    )

    tool = DiscountClaimsCalculatorTool()
    result = await tool.run({"product_id": product.id})
    assert result.ok is True
    assert "₹" in result.data["overlay_text_should_use"]
    assert "$" not in result.data["overlay_text_should_use"]
