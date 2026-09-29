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

from ...core.middleware.logging import get_logger
from ...models.base import async_session_factory
from .url_classifier import detect_url_type

log = get_logger(__name__)


async def run_crawl_and_ingest(session_id: str, url: str) -> None:
    from ...repositories.sqlite.sqlite_brand_repository import SqliteBrandRepository
    from ...repositories.sqlite.sqlite_canvas_repository import SqliteCanvasRepository
    from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
    from ...repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
    from ..knowledge.brand_dna_service import BrandDnaService
    from ..knowledge.guardrail_service import GuardrailService
    from ..knowledge.product_dna_service import ProductDnaService

    async with async_session_factory() as db:
        sessions = SqliteSessionRepository(db)
        session = await sessions.get(session_id)
        if session is None:
            log.warning("crawl_background_session_not_found", extra={"_extra_session_id": session_id})
            return

        url_type = detect_url_type(url)
        try:
            if url_type == "product":
                product_svc = ProductDnaService(SqliteProductRepository(db))
                product, _element_ids = await product_svc.crawl_product_from_url(
                    url=url, session=session, canvas=SqliteCanvasRepository(db)
                )
                linked_ids = list(session.brief.get("product_profile_ids") or [])
                if product.id not in linked_ids:
                    linked_ids.append(product.id)
                session.brief = {**session.brief, "product_profile_ids": linked_ids}
                guardrail_scope = "product"
                guardrail_text = f"Guardrails derived from crawling {url}: {product.attributes.get('summary', '')}"
            else:
                brand_svc = BrandDnaService(SqliteBrandRepository(db))
                brand = await brand_svc.crawl_brand_from_url(url=url, session=session)
                session.brand_profile_id = brand.id
                guardrail_scope = "brand"
                guardrail_text = f"Guardrails derived from crawling {url}: {brand.raw_profile.get('raw_facts', {})}"

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
