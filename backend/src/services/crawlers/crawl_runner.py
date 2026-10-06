"""
The one shared entry point for a Product/Brand crawl, called by both the dedicated
`POST /{session_id}/crawl` route and `session_service.py`'s turn auto-detection — kept as a single
function, not a dispatcher class, per the "straightforward" architecture decision: crawling is
just another capability of the two DNA services that already own this data
(`ProductDnaService.crawl_product_from_url` / `BrandDnaService.crawl_brand_from_url`); this
function only classifies the URL, calls the right one, and does the session bookkeeping + guardrail
re-derivation neither of those services owns.

Always runs as a genuinely detached background task (same reasoning as
`services/orchestration/session_service.py`'s `_run_compliance_background`) — needs its own DB
session, since the request-scoped one that triggered it may already be closed by the time this
actually runs.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from ...core.events import set_current_session
from ...core.middleware.logging import get_logger
from ...models.base import async_session_factory
from .url_classifier import detect_url_type

log = get_logger(__name__)

# Real, live-found race condition (2026-10-05, fidelity audit — exact user report: filling in BOTH
# the brand and product URL fields at workflow-creation time, which fires two crawls concurrently
# against the same brand-new session): each call below opens its OWN `async_session_factory()`
# context (its own identity map, no shared SQLAlchemy session), reads `session.brief`/
# `session.brand_profile_id` into memory, mutates it, then commits — a classic lost-update race.
# Whichever crawl commits second overwrites the other's already-committed field with its own stale
# in-memory snapshot (e.g. the product crawl's commit reverts `brand_profile_id` back to whatever
# it was when THAT task started, silently erasing the brand crawl's result, or vice versa).
# A per-session `asyncio.Lock` serializes the read-mutate-commit critical section for the same
# session_id — cheap and correct for this app's single-process scope (same "fine for a
# single-process POC" tier `core/events.py`'s own event bus already accepts) — so the second
# concurrent crawl re-reads the session AFTER the first has committed and merges on top of it,
# instead of clobbering it.
_session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def run_crawl_and_ingest(session_id: str, url: str, *, url_type: str | None = None) -> None:
    """`url_type` ("brand" | "product"), when given, is an explicit caller decision (which DNA tab
    the user used, or which of the two workflow-creation URL fields they filled in) and always
    wins over the heuristic — `detect_url_type` is a last-resort guess for callers with no real
    tab/field context (e.g. the chat "Add a link" popover), not something that should override a
    caller who already knows. Real, live-found bug this closes (2026-10-05): a product URL
    submitted from the Product DNA tab was silently classified and saved as a BRAND crawl purely
    because its path didn't match any of `detect_url_type`'s known product patterns."""
    # Real, live-found gap (2026-10-05): a crawl runs in its own fresh `asyncio.Task` (created by
    # the request handler, then the handler returns immediately) — `core/events.py`'s `emit()` is
    # a no-op unless `_current_session_id` is set for the CURRENT context, and nothing here ever
    # set it. Every `crawler_started`/`crawler_step`/`crawler_completed` emit anywhere in the crawl
    # path (both DNA services) was silently dropped before ever reaching the SSE queue — not a
    # frontend wiring gap, the events never left the backend. Setting it here, in this task's own
    # context, fixes every crawl emit at once.
    set_current_session(session_id)
    from ...repositories.postgres.postgres_brand_repository import PostgresBrandRepository
    from ...repositories.postgres.postgres_canvas_repository import PostgresCanvasRepository
    from ...repositories.postgres.postgres_product_repository import PostgresProductRepository
    from ...repositories.postgres.postgres_session_repository import PostgresSessionRepository
    from ..knowledge.brand_dna_service import BrandDnaService
    from ..knowledge.guardrail_service import GuardrailService
    from ..knowledge.product_dna_service import ProductDnaService

    async with async_session_factory() as db:
        sessions = PostgresSessionRepository(db)
        session = await sessions.get(session_id)
        if session is None:
            log.warning("crawl_background_session_not_found", extra={"_extra_session_id": session_id})
            return

        resolved_url_type = url_type or detect_url_type(url)
        try:
            # The expensive, genuinely-independent work (scraping the page, the LLM extraction
            # call, downloading images) happens OUTSIDE the lock below, against the snapshot of
            # `session` read above (only its id/user_id are actually used by these calls) — two
            # concurrent crawls for the same session still do their real work in parallel.
            if resolved_url_type == "product":
                product_svc = ProductDnaService(PostgresProductRepository(db))
                product, _element_ids = await product_svc.crawl_product_from_url(
                    url=url, session=session, canvas=PostgresCanvasRepository(db)
                )
                guardrail_scope = "product"
                guardrail_text = f"Guardrails derived from crawling {url}: {product.attributes.get('summary', '')}"
            else:
                brand_svc = BrandDnaService(PostgresBrandRepository(db))
                brand = await brand_svc.crawl_brand_from_url(url=url, session=session)
                guardrail_scope = "brand"
                guardrail_text = f"Guardrails derived from crawling {url}: {brand.raw_profile.get('raw_facts', {})}"

            # Only the session read-mutate-commit itself is serialized, and re-reads the row FRESH
            # (`db.refresh`, not the identity-map-cached `session` object from above) right before
            # mutating it — so a sibling crawl that committed first (e.g. the brand crawl, while
            # this product crawl was still scraping/extracting) is seen and merged onto, not
            # silently reverted by this task's now-stale snapshot.
            async with _session_locks[session_id]:
                await db.refresh(session)
                if resolved_url_type == "product":
                    linked_ids = list(session.brief.get("product_profile_ids") or [])
                    if product.id not in linked_ids:
                        linked_ids.append(product.id)
                    session.brief = {**session.brief, "product_profile_ids": linked_ids}
                else:
                    session.brand_profile_id = brand.id

                crawled_urls = list(session.brief.get("crawled_urls") or [])
                if url not in crawled_urls:
                    crawled_urls.append(url)
                session.brief = {**session.brief, "crawled_urls": crawled_urls}
                await sessions.update(session)

            guardrail_svc = GuardrailService(sessions)
            await guardrail_svc.add_rule_from_user_context(
                session_id, guardrail_text, guardrail_scope, "crawler"
            )
        except Exception as exc:
            log.error(
                "crawl_background_failed",
                extra={"_extra_session_id": session_id, "_extra_url": url, "_extra_error": str(exc)},
            )
