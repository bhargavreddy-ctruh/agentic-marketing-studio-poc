"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found bug (2026-09-30): a user referenced an uploaded "Nothing Phone 4a" image and
asked for a marketing composite; the generated image's rendered text label read
"Nothing Phone (2a)" instead. Root cause: `ProductDnaService.upsert_product_from_chat`/
`upsert_product_from_image` refreshed a matched product's `.attributes` on a match but never its
`.name` — unlike `onboard_product`'s own update path, which already renames. Verifies the fix:
a matched product's name now updates to the newly-extracted name, and the re-indexed text reflects it.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.models.product_profile import ProductProfileModel
from src.providers.llm.base import LLMResult
from src.services.knowledge.product_dna_service import ProductDnaService


class _FakeProductRepository:
    def __init__(self, products: dict[str, ProductProfileModel]):
        self._products = products

    async def add(self, product: ProductProfileModel) -> ProductProfileModel:
        self._products[product.id] = product
        return product

    async def get(self, product_id: str) -> ProductProfileModel | None:
        return self._products.get(product_id)

    async def list_all(self) -> list[ProductProfileModel]:
        return list(self._products.values())

    async def update(self, product: ProductProfileModel) -> ProductProfileModel:
        return await self.add(product)

    async def delete(self, product_id: str) -> None:
        self._products.pop(product_id, None)


@pytest.mark.asyncio
async def test_upsert_product_from_chat_renames_matched_product():
    existing = ProductProfileModel(
        id="p1", user_id="u1", name="Nothing Phone (2a) in Blue",
        attributes={"summary": "stale 2a summary"}, indexed=True,
    )
    repo = _FakeProductRepository({"p1": existing})
    svc = ProductDnaService(repo)

    llm_response = LLMResult(
        text=json.dumps({
            "is_product_related": True,
            "matched_product_id": "p1",
            "name": "Nothing Phone 4a",
            "summary": "the real 4a facts",
            "must_show": [], "never_show": [], "claims_allowed": [], "claims_disallowed": [],
            "label_visibility": "",
        }),
        model="test-model",
    )

    with patch("src.services.knowledge.product_dna_service.get_llm_provider") as mock_llm, \
         patch("src.services.knowledge.product_dna_service.get_knowledge_provider") as mock_knowledge:
        mock_llm.return_value.complete = AsyncMock(return_value=llm_response)
        mock_knowledge.return_value.index_document = AsyncMock()

        product = await svc.upsert_product_from_chat(
            user_id="u1", existing_products=[existing], raw_text="it's actually the 4a model",
        )

        assert product is not None
        assert product.name == "Nothing Phone 4a"  # renamed, not stuck on "(2a) in Blue"

        # The re-indexed document text must reflect the corrected name, not the stale one.
        index_call = mock_knowledge.return_value.index_document.await_args
        assert index_call.kwargs["text"].startswith("Product: Nothing Phone 4a")


@pytest.mark.asyncio
async def test_upsert_product_from_chat_keeps_existing_name_when_not_restated():
    existing = ProductProfileModel(
        id="p1", user_id="u1", name="Nothing Phone 4a",
        attributes={"summary": "already correct"}, indexed=True,
    )
    repo = _FakeProductRepository({"p1": existing})
    svc = ProductDnaService(repo)

    # A short refinement message that doesn't restate the product's name at all.
    llm_response = LLMResult(
        text=json.dumps({
            "is_product_related": True,
            "matched_product_id": "p1",
            "name": "",
            "summary": "now also mention the camera",
            "must_show": ["48MP camera"], "never_show": [], "claims_allowed": [], "claims_disallowed": [],
            "label_visibility": "",
        }),
        model="test-model",
    )

    with patch("src.services.knowledge.product_dna_service.get_llm_provider") as mock_llm, \
         patch("src.services.knowledge.product_dna_service.get_knowledge_provider") as mock_knowledge:
        mock_llm.return_value.complete = AsyncMock(return_value=llm_response)
        mock_knowledge.return_value.index_document = AsyncMock()

        product = await svc.upsert_product_from_chat(
            user_id="u1", existing_products=[existing], raw_text="also mention the 48MP camera",
        )

        assert product.name == "Nothing Phone 4a"  # unchanged, never blanked
