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

import httpx

from ...core.events import emit
from ...core.exceptions import Forbidden, NotFoundError, ProviderUnavailable
from ...core.json_extract import extract_json
from ...core.local_storage import save_asset
from ...core.middleware.logging import get_logger
from ...core.mime_sniff import sniff_image_mime
from ...models.brand_profile import BrandProfileModel
from ...models.session import SessionModel
from ...providers.crawlers.firecrawl_provider import scrape_url_via_firecrawl
from ...providers.crawlers.playwright_scraper import scrape_url
from ...providers.crawlers.types import ScrapedPage
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...providers.llm.base import ModelTier
from ...providers.llm.ollama import get_ollama_provider
from ...providers.llm.router import get_llm_provider
from ...repositories.base import BrandRepository
from .guardrail_synthesizer import synthesize_guardrails

log = get_logger(__name__)

_FONT_MIME_BY_EXT = {
    "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf",
}

_CRAWL_SYSTEM_PROMPT = """You are the Brand DNA extractor, reading a scraped web page (a company's
site, About page, or storefront). Extract what's actually present — never invent facts.

Return ONLY JSON:
{
  "name": "the brand/company name, or empty string if unclear",
  "mission": "one or two sentences, or empty string",
  "tone_of_voice": ["short adjectives, e.g. Bold, Playful"],
  "target_audience": "short phrase, or empty string",
  "value_props": ["short phrases"]
}
"""


async def _scrape_with_fallback(url: str) -> ScrapedPage:
    try:
        return await scrape_url(url)
    except ProviderUnavailable as exc:
        log.warning("crawler_playwright_failed_falling_back_to_firecrawl", extra={"_extra_url": url, "_extra_error": str(exc)})
        return await scrape_url_via_firecrawl(url)


async def _extract_brand_facts(page: ScrapedPage) -> dict:
    context = f"Page title: {page.title}\nURL: {page.url}\nVisible text:\n{page.text_content}"
    try:
        result = await get_ollama_provider().complete(
            tier=ModelTier.TIER_1, system=_CRAWL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}], max_tokens=1024,
        )
    except ProviderUnavailable as exc:
        log.warning("crawler_ollama_failed_falling_back_to_llm_router", extra={"_extra_error": str(exc)})
        result = await get_llm_provider().complete(
            tier=ModelTier.TIER_1, system=_CRAWL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}], max_tokens=1024,
        )
    return extract_json(result.text)


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

    async def crawl_brand_from_url(self, *, url: str, session: SessionModel) -> BrandProfileModel:
        """Brand crawler section (2026-09-28) — scrapes `url` (Playwright, Firecrawl on failure),
        extracts brand facts (Ollama gemma2:2b primary, Groq/Replicate fallback via
        `get_llm_provider()`), then reuses `onboard_brand` exactly as the manual DNA-tab form does:
        updates the session's ONE existing brand row in place if it already has one, never spawns a
        second brand for the same session (same "one brand per session" rule the rest of this app
        already enforces)."""
        emit("crawler_started", session_id=session.id, url=url, url_type="brand")
        try:
            # Real per-stage progress (2026-10-05, explicit user ask: "show what's being
            # extracted so the user doesn't feel left out") — `crawler_step` used to only ever
            # fire on failure; these are the real stages this function actually goes through.
            emit("crawler_step", session_id=session.id, url=url, url_type="brand", status="scraping_page")
            page = await _scrape_with_fallback(url)
            emit("crawler_step", session_id=session.id, url=url, url_type="brand", status="extracting_facts")
            facts = await _extract_brand_facts(page)
            name = str(facts.get("name") or "Unnamed brand").strip()[:255] or "Unnamed brand"
            raw_facts = {
                "mission": facts.get("mission", ""),
                "tone_of_voice": facts.get("tone_of_voice", []),
                "target_audience": facts.get("target_audience", ""),
                "value_props": facts.get("value_props", []),
                "dominant_colors": page.dominant_colors,
            }
            brand = await self.onboard_brand(
                user_id=session.user_id, name=name, raw_facts=raw_facts,
                brand_id=session.brand_profile_id,
            )
            # Real, live-found gap (2026-09-28, explicit user report: "brand dna scraper is not
            # scraping the brand logo") — onboard_brand only ever persists raw_facts text; nothing
            # populated `logo_storage_ref` (the same field the manual "Brand Logo (PNG)" upload
            # sets). Best-effort: a missing/unreachable logo never fails the whole crawl.
            if page.logo_url:
                emit("crawler_step", session_id=session.id, url=url, url_type="brand", status="downloading_logo")
                try:
                    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                        resp = await client.get(page.logo_url)
                        resp.raise_for_status()
                        logo_bytes = resp.content
                    mime_type = sniff_image_mime(logo_bytes)
                    storage_ref = await save_asset(
                        logo_bytes, mime_type,
                        metadata={"source": "brand_crawl", "brand_id": brand.id, "url": page.logo_url},
                    )
                    brand.logo_storage_ref = storage_ref
                    brand = await self._brands.add(brand)
                except Exception as exc:
                    log.warning("crawler_logo_download_failed", extra={"_extra_url": page.logo_url, "_extra_error": str(exc)})

            # Real, live-found gap (2026-09-28, explicit user report: "it should extract font
            # also from the website") — same reasoning as the logo above: `font_storage_refs` is a
            # real, existing field (set by the manual "Custom Font (TTF/OTF)" upload) that a crawl
            # never populated. `page.fonts` is real @font-face rules the site itself declares, not
            # a guess — cap at 2 so one page with a huge type system doesn't bloat storage.
            if page.fonts:
                emit("crawler_step", session_id=session.id, url=url, url_type="brand", status="downloading_fonts")
                font_refs = dict(brand.font_storage_refs or {})
                for family, font_url in page.fonts[:2]:
                    try:
                        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                            resp = await client.get(font_url)
                            resp.raise_for_status()
                            font_bytes = resp.content
                        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                        if not content_type or content_type == "application/octet-stream":
                            content_type = _FONT_MIME_BY_EXT.get(font_url.rsplit(".", 1)[-1].lower().split("?")[0], "font/ttf")
                        storage_ref = await save_asset(
                            font_bytes, content_type,
                            metadata={"source": "brand_crawl", "brand_id": brand.id, "url": font_url, "family": family},
                        )
                        font_refs[family] = storage_ref
                    except Exception as exc:
                        log.warning("crawler_font_download_failed", extra={"_extra_url": font_url, "_extra_error": str(exc)})
                if font_refs != (brand.font_storage_refs or {}):
                    brand.font_storage_refs = font_refs
                    brand = await self._brands.add(brand)

            emit("crawler_completed", session_id=session.id, url=url, url_type="brand", brand_profile_id=brand.id)
            return brand
        except Exception as exc:
            emit("crawler_step", session_id=session.id, url=url, url_type="brand", status="failed", error=str(exc))
            raise

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
