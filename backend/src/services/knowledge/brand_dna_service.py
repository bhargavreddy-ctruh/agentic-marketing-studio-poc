"""
Brand DNA Agent — Architecture.md section 2.2, section 6: ingest a brand once, produce the Brand
DNA profile every visual specialist reads from (via the `brand_kit_lookup` tool). This is the
first time in this POC a brand actually gets onboarded for real — before this, `brand_kit_lookup`
always correctly returned "configured: false" because nothing had ever been indexed.

Business logic lives here, not in the route handler (Rules.md section 2). Depends on the
BrandRepository Protocol, never a concrete SQLite class (Dependency Inversion).
"""
from __future__ import annotations

import uuid

from ...core.exceptions import Forbidden, NotFoundError
from ...core.middleware.logging import get_logger
from ...models.brand_profile import BrandProfileModel
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...repositories.base import BrandRepository
from .guardrail_synthesizer import synthesize_guardrails

log = get_logger(__name__)


def _build_index_text(name: str, raw_facts: dict, guardrails: dict) -> str:
    return (
        f"Brand: {name}\n"
        f"Raw facts: {raw_facts}\n"
        f"Guardrail summary: {guardrails.get('summary', '')}\n"
        f"Visual rules: {guardrails.get('visual', [])}\n"
        f"Price overlay rules: {guardrails.get('price_overlay', [])}"
    )


class BrandDnaService:
    def __init__(self, brands: BrandRepository):
        self._brands = brands

    async def onboard_brand(self, *, user_id: str, name: str, raw_facts: dict) -> BrandProfileModel:
        guardrails = await synthesize_guardrails(raw_facts=raw_facts)

        brand = BrandProfileModel(
            id=uuid.uuid4().hex,
            user_id=user_id,
            name=name,
            raw_profile={"raw_facts": raw_facts, "guardrails": guardrails},
            indexed=False,
        )
        brand = await self._brands.add(brand)

        # Real LlamaIndex ingestion — not a static config file, per the user's explicit decision
        # to keep the real retrieval system from day one (Architecture.md section 3).
        # Disclosed, deliberate scope cut (Tasks_Workflows.md #3): the index itself is one flat
        # collection with no per-owner filter at retrieval time (brand_kit_lookup.py queries
        # `collection="brand"` with no owner scoping) — real ownership now exists at the SQL/HTTP
        # layer (who can onboard/list/read a BrandProfileModel row), but cross-user RAG retrieval
        # isolation is a separate, larger piece of work not done in this pass.
        await get_knowledge_provider().index_document(
            collection="brand", doc_id=brand.id, text=_build_index_text(name, raw_facts, guardrails)
        )
        brand.indexed = True
        return await self._brands.add(brand)

    async def get_brand(self, brand_id: str, *, user_id: str) -> BrandProfileModel:
        brand = await self._brands.get(brand_id)
        if brand is None:
            raise NotFoundError("Brand", brand_id)
        if brand.user_id != user_id:
            raise Forbidden("This brand profile belongs to a different user")
        return brand

    async def list_brands(self, *, user_id: str) -> list[BrandProfileModel]:
        return await self._brands.list_for_user(user_id)


async def reindex_all_brands(brands: BrandRepository) -> None:
    """
    Rehydrates LlamaIndex's in-memory Brand DNA collection from the real, persisted SQL records on
    every app startup. Real gap found via live testing (Memory.md, Phase 3): `LlamaIndexKnowledge
    Provider`'s indices are in-memory-only (Phase 0's own documented scope), so a server restart
    silently lost every onboarded brand's searchability — `brand_kit_lookup` would then wrongly
    report "configured: false" for a brand that's still very much in the database. This does not
    re-run guardrail synthesis (no new LLM call, no cost) — it just re-indexes the guardrails
    already computed and stored at onboarding time.
    """
    knowledge = get_knowledge_provider()
    for brand in await brands.list_all():
        if not brand.indexed:
            continue
        raw_facts = brand.raw_profile.get("raw_facts", {})
        guardrails = brand.raw_profile.get("guardrails", {})
        await knowledge.index_document(
            collection="brand", doc_id=brand.id, text=_build_index_text(brand.name, raw_facts, guardrails)
        )
    log.info("brand_reindex_complete")
