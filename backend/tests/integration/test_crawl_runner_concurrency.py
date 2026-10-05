"""Integration test — real SQLite, per Architecture.md's tests/integration/ scope.

Fidelity audit (2026-10-05), two real, live-found bugs confirmed via live reproduction:
1. The backend used to GUESS brand vs product purely from the URL's own shape
   (`url_classifier.detect_url_type`), defaulting to "brand" whenever nothing matched — a real
   product URL submitted from the Product DNA tab got silently saved as a brand crawl instead.
   `run_crawl_and_ingest`'s new explicit `url_type` kwarg must always win over the heuristic.
2. Two concurrent crawls against the same brand-new session (the workflow-creation dialog fires a
   brand crawl and a product crawl together) raced on a read-mutate-commit of `session.brief`/
   `session.brand_profile_id` — whichever committed second silently reverted the other's field.
   The per-session `asyncio.Lock` + `db.refresh()` fix must make both survive.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.base import Base
from src.models.brand_profile import BrandProfileModel
from src.models.product_profile import ProductProfileModel
from src.models.session import SessionModel
from src.repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
from src.services.crawlers import crawl_runner


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _make_session(session_factory, user_id: str) -> str:
    async with session_factory() as db:
        repo = SqliteSessionRepository(db)
        created = await repo.add(SessionModel(id=uuid.uuid4().hex, user_id=user_id, status="ideating", brief={}))
        return created.id


@pytest.mark.asyncio
async def test_explicit_url_type_overrides_heuristic_guess(session_factory, monkeypatch):
    monkeypatch.setattr(crawl_runner, "async_session_factory", session_factory)
    user_id = uuid.uuid4().hex
    session_id = await _make_session(session_factory, user_id)

    # A real product URL, but shaped so `detect_url_type` would guess "brand" (no product path
    # segment, no sku-looking segment) — e.g. "https://example.com/airpods-pro/".
    fake_product = ProductProfileModel(id=uuid.uuid4().hex, user_id=user_id, name="Fake Product", attributes={})

    with (
        patch(
            "src.services.knowledge.product_dna_service.ProductDnaService.crawl_product_from_url",
            new=AsyncMock(return_value=(fake_product, [])),
        ) as product_crawl,
        patch(
            "src.services.knowledge.brand_dna_service.BrandDnaService.crawl_brand_from_url",
            new=AsyncMock(),
        ) as brand_crawl,
        patch("src.services.knowledge.guardrail_service.GuardrailService.add_rule_from_user_context", new=AsyncMock()),
    ):
        await crawl_runner.run_crawl_and_ingest(session_id, "https://example.com/airpods-pro/", url_type="product")

    product_crawl.assert_awaited_once()
    brand_crawl.assert_not_awaited()

    async with session_factory() as db:
        session = await SqliteSessionRepository(db).get(session_id)
        assert session.brief.get("product_profile_ids") == [fake_product.id]


@pytest.mark.asyncio
async def test_concurrent_brand_and_product_crawls_do_not_clobber_each_other(session_factory, monkeypatch):
    import asyncio

    monkeypatch.setattr(crawl_runner, "async_session_factory", session_factory)
    user_id = uuid.uuid4().hex
    session_id = await _make_session(session_factory, user_id)

    fake_product = ProductProfileModel(id=uuid.uuid4().hex, user_id=user_id, name="Fake Product", attributes={})
    fake_brand = BrandProfileModel(id=uuid.uuid4().hex, user_id=user_id, name="Fake Brand", raw_profile={"raw_facts": {}})

    async def _slow_product_crawl(*args, **kwargs):
        await asyncio.sleep(0.05)
        return fake_product, []

    async def _slow_brand_crawl(*args, **kwargs):
        await asyncio.sleep(0.02)
        return fake_brand

    with (
        patch(
            "src.services.knowledge.product_dna_service.ProductDnaService.crawl_product_from_url",
            new=AsyncMock(side_effect=_slow_product_crawl),
        ),
        patch(
            "src.services.knowledge.brand_dna_service.BrandDnaService.crawl_brand_from_url",
            new=AsyncMock(side_effect=_slow_brand_crawl),
        ),
        patch("src.services.knowledge.guardrail_service.GuardrailService.add_rule_from_user_context", new=AsyncMock()),
    ):
        # Fired together, same as the workflow-creation dialog's own two unawaited crawlUrl() calls.
        await asyncio.gather(
            crawl_runner.run_crawl_and_ingest(session_id, "https://example.com/product-page", url_type="product"),
            crawl_runner.run_crawl_and_ingest(session_id, "https://example.com/", url_type="brand"),
        )

    async with session_factory() as db:
        session = await SqliteSessionRepository(db).get(session_id)
        # Both fields must survive — neither crawl's commit may have reverted the other's.
        assert session.brief.get("product_profile_ids") == [fake_product.id]
        assert session.brand_profile_id == fake_brand.id
