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

    async def onboard_brand(
        self, *, user_id: str, name: str, raw_facts: dict, brand_id: str | None = None, merge: bool = True,
    ) -> BrandProfileModel:
        """`brand_id` (2026-09-25, real requirement: "brand dna is same across all sessions of a
        user" — the "DNA" tab's manual Brand DNA form must update the user's ONE existing brand
        row, not spawn a new one per session) — when given, updates that row in place; existing
        `raw_facts` keys not present in this call's `raw_facts` are kept, not dropped, so editing
        from one session doesn't silently erase facts entered from another.

        `merge` (2026-09-25, real requirement: "give the user option to edit" the brand's actual
        fact list) — the merge-only behavior above is correct for the DNA tab's simplified 3-field
        form (which only ever shows/edits a SUBSET of the real facts, so it must never silently wipe
        the rest), but is WRONG for a caller presenting the user the COMPLETE fact list to edit
        directly: a key the user deliberately removed there would otherwise survive the merge and
        silently reappear. `merge=False` replaces `raw_facts` outright with exactly what's given."""
        existing = await self._brands.get(brand_id) if brand_id else None
        existing_facts = existing.raw_profile.get("raw_facts", {}) if existing else {}
        merged_facts = {**existing_facts, **raw_facts} if merge else dict(raw_facts)
        guardrails = await synthesize_guardrails(raw_facts=merged_facts)

        if existing:
            existing.name = name
            existing.raw_profile = {"raw_facts": merged_facts, "guardrails": guardrails}
            brand = await self._brands.add(existing)
        else:
            brand = BrandProfileModel(
                id=uuid.uuid4().hex,
                user_id=user_id,
                name=name,
                raw_profile={"raw_facts": merged_facts, "guardrails": guardrails},
                indexed=False,
            )
            brand = await self._brands.add(brand)

        # Real LlamaIndex ingestion — not a static config file, per the user's explicit decision
        # to keep the real retrieval system from day one (Architecture.md section 3).
        # Per-user isolation (verified live, 2026-09-25 — an earlier comment here claiming
        # `brand_kit_lookup.py` queries a flat, unscoped `"brand"` collection was stale/wrong):
        # both indexing here and retrieval in `tools/brand_kit_lookup.py` use the exact same
        # `f"brand_{user_id}"` collection name — confirmed no cross-user leak in that path. The
        # real, separate gap that DID exist (compliance checkers calling the lookup tool with no
        # `user_id` at all, always hitting its "not configured" early-exit) is fixed in
        # `compliance/compliance_gate.py`/`brand_consistency_checker.py`.
        await get_knowledge_provider().index_document(
            collection=f"brand_{user_id}", doc_id=brand.id, text=_build_index_text(name, merged_facts, guardrails)
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
        if brand.user_id:
            await knowledge.index_document(
                collection=f"brand_{brand.user_id}", doc_id=brand.id, text=_build_index_text(brand.name, raw_facts, guardrails)
            )
    log.info("brand_reindex_complete")
