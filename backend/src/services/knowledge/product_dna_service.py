"""
Product Attribute Extractor / Product DNA Agent — Architecture.md section 2.2, section 6: a
near-direct port of the existing agentic_flow codebase's `run_product_intelligence`
(`hitl_agents.py`), which already produces the exact `mustShow`/`neverShow`/`claimsAllowed`/
`claimsDisallowed`/`labelVisibility` shape the guardrail-first pattern needs (Rules.md section 6).

Three ways Product DNA gets created/refined, all converging on the same `attributes` shape:
`onboard_product` (the manual onboarding form), `upsert_product_from_chat` (2026-09-25, a chat
message describing a product), and `upsert_product_from_image` (2026-09-25, real requirement:
"evolve product dna not only from the chat but also from what's on the canvas" — an image placed
directly on the canvas, via `providers/llm/vision.py`'s vision-capable call, the same one this
app's compliance checkers already use).
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx

from ...core.events import emit
from ...core.exceptions import NotFoundError, ProviderUnavailable, SpecialistFailed
from ...core.json_extract import extract_json
from ...core.local_storage import public_url, save_asset
from ...core.middleware.logging import get_logger
from ...core.mime_sniff import sniff_image_mime
from ...models.canvas_element import CanvasElementModel
from ...models.product_profile import ProductProfileModel
from ...models.session import SessionModel
from ...providers.crawlers.firecrawl_provider import scrape_url_via_firecrawl
from ...providers.crawlers.playwright_scraper import scrape_url
from ...providers.crawlers.types import ScrapedPage
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...providers.llm.base import ModelTier
from ...providers.llm.ollama import get_ollama_provider
from ...providers.llm.router import get_llm_provider
from ...providers.llm.vision import complete_with_vision
from ...providers.observability.langsmith import traceable
from ...repositories.base import CanvasRepository, ProductRepository

log = get_logger(__name__)

_MAX_CRAWLED_IMAGES = 5

_CRAWL_SYSTEM_PROMPT = """You are the Product Attribute Extractor, reading a scraped product page.
Extract what's actually present — never invent a price, discount, or claim not actually stated.

Return ONLY JSON:
{
  "name": "the product's name, or empty string if unclear",
  "summary": "one or two sentences describing the product for generation purposes",
  "price": number or null,
  "discount_percent": number or null,
  "must_show": ["short phrases"],
  "never_show": ["short phrases"],
  "claims_allowed": ["short phrases"],
  "claims_disallowed": ["short phrases"],
  "label_visibility": "a short instruction, or empty string if not applicable"
}
"""


async def _scrape_with_fallback(url: str) -> ScrapedPage:
    try:
        return await scrape_url(url)
    except ProviderUnavailable as exc:
        log.warning("crawler_playwright_failed_falling_back_to_firecrawl", extra={"_extra_url": url, "_extra_error": str(exc)})
        return await scrape_url_via_firecrawl(url)


async def _extract_product_facts(page: ScrapedPage) -> dict:
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


def _build_index_text(name: str, attributes: dict) -> str:
    return (
        f"Product: {name}\nSummary: {attributes.get('summary', '')}\n"
        f"Price: {attributes.get('price')}\nDiscount: {attributes.get('discount_percent')}%\n"
        f"Must show: {attributes.get('must_show', [])}\nNever show: {attributes.get('never_show', [])}\n"
        f"Claims allowed: {attributes.get('claims_allowed', [])}\n"
        f"Claims disallowed: {attributes.get('claims_disallowed', [])}"
    )

_SYSTEM_PROMPT = """You are the Product Attribute Extractor for a marketing campaign.

Given a product's name, description, and optional price/discount, derive the constraints
specialists must bind exact facts from rather than free-generating: what must always be shown,
what must never be shown, which claims are allowed, which are disallowed, and label visibility
requirements. Only state a price or discount if one was actually given — never invent a number.

Return ONLY JSON:
{
  "summary": "one or two sentences describing the product for generation purposes",
  "must_show": ["short phrases"],
  "never_show": ["short phrases"],
  "claims_allowed": ["short phrases"],
  "claims_disallowed": ["short phrases"],
  "label_visibility": "a short instruction, or empty string if not applicable"
}
"""

# Real requirement (2026-09-25, explicit user ask): a session's chat can describe several DISTINCT
# products over its lifetime, and the same product's DNA should keep refining as the user says more
# about it — never silently overwritten by an unrelated later product. This prompt hands the model
# every product the session already knows about (name + summary) so it can decide which one (if
# any) THIS message is about, rather than this service blindly assuming "one product per session".
_CHAT_SYSTEM_PROMPT = """You are the Product DNA extractor for a marketing campaign, updating live
from an ongoing chat conversation.

You are given the products this session already knows about (each with an id, name and summary)
and a new chat message. Decide:
1. Does this message contain any real, concrete information about a product at all — a name,
   specs, features, price, what should/shouldn't be shown or claimed? If the message is a bare
   command, approval, style tweak, or anything with no actual product facts in it (e.g. "approve",
   "make it more vibrant", "generate a video with voiceover"), set "is_product_related": false and
   leave every other field empty/null — do not invent a product that isn't really there.
2. If it IS product-related: does it describe/refine one of the ALREADY-KNOWN products, or a
   genuinely NEW, DIFFERENT product this session hasn't seen before? Only treat it as the same
   product if it's clearly the same subject — a different name, category or use-case means it's new.
3. Extract the full Product DNA from this message: what must always be shown, what must never be
   shown, which claims are allowed/disallowed, label visibility, price/discount if actually stated.
   Never invent a price, discount, or any fact not actually in the message.

Return ONLY JSON:
{
  "is_product_related": true or false,
  "matched_product_id": "the id of the already-known product this refers to, or null if new",
  "name": "the product's name — required when is_product_related is true and matched_product_id is null",
  "summary": "one or two sentences describing the product for generation purposes",
  "price": number or null,
  "discount_percent": number or null,
  "must_show": ["short phrases"],
  "never_show": ["short phrases"],
  "claims_allowed": ["short phrases"],
  "claims_disallowed": ["short phrases"],
  "label_visibility": "a short instruction, or empty string if not applicable"
}
"""

# Real requirement (2026-09-25, explicit user ask: "evolve product dna not only from the chat but
# also from what's on the canvas"). Only fires for a user's own direct canvas upload (never a
# specialist's generated creative — that's campaign output, not a product declaration) — see
# `api/v1/canvas/routes.py::create_element`. `is_product_photo` lets the model correctly no-op on
# a mood-board reference, a background texture, or anything else that isn't actually a product,
# instead of this service ever treating every upload as automatic Product DNA.
_IMAGE_SYSTEM_PROMPT = """You are the Product DNA extractor for a marketing campaign, looking at an
image the user just placed on the campaign canvas.

You are given the products this session already knows about (each with an id, name and summary)
and the uploaded image. Decide:
1. Is this image actually a photo of A PRODUCT (e.g. a shoe, a phone, packaging, a real item this
   campaign is about)? If it's clearly NOT a product photo (a mood-board/style reference, a plain
   background or texture, an unrelated or unreadable image), set "is_product_photo": false and
   leave every other field empty/null — do not guess a product into existence.
2. If it IS a product photo: does it show one of the ALREADY-KNOWN products, or a genuinely NEW,
   DIFFERENT one this session hasn't seen before?
3. Extract Product DNA ONLY from what's actually visible in the image — visible colors, materials,
   design details go in "must_show". Never invent a price, discount, or a claim you can't see.

Return ONLY JSON:
{
  "is_product_photo": true or false,
  "matched_product_id": "the id of the already-known product this shows, or null if new",
  "name": "the product's name — required when is_product_photo is true and matched_product_id is null",
  "summary": "one or two sentences describing the product for generation purposes",
  "must_show": ["short phrases"],
  "never_show": ["short phrases"],
  "claims_allowed": ["short phrases"],
  "claims_disallowed": ["short phrases"],
  "label_visibility": "a short instruction, or empty string if not applicable"
}
"""


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()][:12]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _build_attributes(
    parsed: dict, *, price: float | None, discount_percent: float | None,
    description_fallback: str, existing: dict | None = None,
) -> dict:
    """Shared attribute-dict shape for both `onboard_product` (fresh, no `existing`) and
    `upsert_product_from_chat` (refining a known product's DNA, `existing` = its current
    attributes) — a field the model didn't mention this round falls back to what was already known
    rather than being wiped."""
    existing = existing or {}
    return {
        "summary": str(parsed.get("summary") or existing.get("summary") or description_fallback[:400]),
        "price": price if price is not None else existing.get("price"),
        "discount_percent": discount_percent if discount_percent is not None else existing.get("discount_percent"),
        "must_show": _str_list(parsed.get("must_show")) or existing.get("must_show", []),
        "never_show": _str_list(parsed.get("never_show")) or existing.get("never_show", []),
        "claims_allowed": _str_list(parsed.get("claims_allowed")) or existing.get("claims_allowed", []),
        "claims_disallowed": _str_list(parsed.get("claims_disallowed")) or existing.get("claims_disallowed", []),
        "label_visibility": str(parsed.get("label_visibility") or existing.get("label_visibility", "")),
    }


class ProductDnaService:
    def __init__(self, products: ProductRepository):
        self._products = products

    @traceable(name="product_dna_service")
    async def onboard_product(
        self, *, user_id: str, name: str, description: str, price: float | None, discount_percent: float | None,
        product_id: str | None = None,
    ) -> ProductProfileModel:
        """`product_id` (2026-09-25, real requirement: connect the "DNA" tab's manual Product DNA
        form to real, session-scoped Product DNA instead of the old loose-rule-text mechanism) —
        when given, updates that EXISTING row in place instead of creating a new one, same
        update-vs-create semantics `upsert_product_from_chat` already uses, just deterministic here
        since the caller (the manual form) already knows exactly which product it's editing."""
        llm = get_llm_provider()
        context = (
            f"Product name: {name}\nDescription:\n{description}\n"
            f"Price: {price if price is not None else 'not given'}\n"
            f"Discount: {discount_percent if discount_percent is not None else 'not given'}%"
        )
        try:
            result = await llm.complete(
                tier=ModelTier.TIER_2,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
                max_tokens=1536,
            )
            parsed = extract_json(result.text)
        except Exception as exc:
            log.error("product_dna_extraction_failed", extra={"_extra_error": str(exc)})
            raise SpecialistFailed("product_dna_service", str(exc)) from exc

        existing = await self._products.get(product_id) if product_id else None
        attributes = _build_attributes(
            parsed, price=price, discount_percent=discount_percent, description_fallback=description,
            existing=existing.attributes if existing else None,
        )

        if existing:
            existing.name = name
            existing.attributes = attributes
            product = await self._products.add(existing)
        else:
            product = ProductProfileModel(
                id=uuid.uuid4().hex, user_id=user_id, name=name, attributes=attributes, indexed=False
            )
            product = await self._products.add(product)

        await get_knowledge_provider().index_document(
            collection=f"product_{user_id}", doc_id=product.id, text=_build_index_text(name, attributes)
        )
        product.indexed = True
        return await self._products.add(product)

    @traceable(name="product_dna_chat_upsert")
    async def upsert_product_from_chat(
        self, *, user_id: str | None, existing_products: list[ProductProfileModel], raw_text: str,
    ) -> ProductProfileModel | None:
        """The chat-driven counterpart to `onboard_product` (2026-09-25, real requirement: "when
        products are added to a session, automatically product dna should be updated" — and,
        distinctly, "there might be many products in one session"). Given every product this
        session already knows about, an LLM call decides whether the new chat text refines one of
        them or introduces a genuinely new one, then this method creates-or-updates the real
        `ProductProfileModel` row and re-indexes it into LlamaIndex — same durable persistence
        `onboard_product` already gives the manual form, just triggered by chat instead.

        Real, live-found reliability bug fixed here (2026-09-25, explicit user report: "product
        dna... are not auto filled" — confirmed live by reading a real user's chat_turns: a message
        that plainly listed full product specs, RAM/ROM/camera details, never produced any Product
        DNA). Root cause: this used to be called ONLY when `ideation_service.py`'s
        `new_guardrails` field happened to contain a `source: "product"` entry — a side-instruction
        bolted onto an LLM call whose real job is judging message CLARITY, not extracting product
        facts, and it silently skipped the extraction on real, product-rich messages more often
        than not (the same "an LLM instruction isn't a guarantee" failure mode this codebase has
        hit and fixed before). Callers now invoke this directly, unconditionally, for every
        substantive chat message (see `session_service.py`) — its OWN `is_product_related` field
        (not a caller's guess beforehand) is what correctly no-ops on "approve"/"make it more
        vibrant"/etc, so the reliability gap closes without either missing real product messages or
        fabricating products from unrelated ones. Returns `None` on a genuine no-op."""
        llm = get_llm_provider()
        known = [
            {"id": p.id, "name": p.name, "summary": p.attributes.get("summary", "")}
            for p in existing_products
        ]
        context = f"Already-known products in this session:\n{known}\n\nNew chat message:\n{raw_text}"
        try:
            result = await llm.complete(
                tier=ModelTier.TIER_2,
                system=_CHAT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
                max_tokens=1536,
            )
            parsed = extract_json(result.text)
        except Exception as exc:
            log.error("product_dna_chat_extraction_failed", extra={"_extra_error": str(exc)})
            raise SpecialistFailed("product_dna_service", str(exc)) from exc

        if not parsed.get("is_product_related"):
            return None

        matched_id = parsed.get("matched_product_id")
        matched = next((p for p in existing_products if p.id == matched_id), None) if matched_id else None

        attributes = _build_attributes(
            parsed,
            price=parsed.get("price"),
            discount_percent=parsed.get("discount_percent"),
            description_fallback=raw_text,
            existing=matched.attributes if matched else None,
        )

        if matched:
            matched.attributes = attributes
            product = await self._products.add(matched)
        else:
            name = str(parsed.get("name") or "Unnamed product").strip()[:255]
            product = ProductProfileModel(
                id=uuid.uuid4().hex, user_id=user_id, name=name, attributes=attributes, indexed=False
            )
            product = await self._products.add(product)

        await get_knowledge_provider().index_document(
            collection=f"product_{user_id}", doc_id=product.id, text=_build_index_text(product.name, attributes)
        )
        product.indexed = True
        return await self._products.add(product)

    @traceable(name="product_dna_image_upsert")
    async def upsert_product_from_image(
        self, *, user_id: str | None, existing_products: list[ProductProfileModel],
        image_bytes: bytes, mime_type: str, storage_ref: str,
    ) -> ProductProfileModel | None:
        """The canvas-driven counterpart to `upsert_product_from_chat` (2026-09-25, real
        requirement: "evolve product dna not only from the chat but also from what's on the
        canvas"). Returns `None` (a real no-op, not an error) when the image genuinely isn't a
        product photo — see `_IMAGE_SYSTEM_PROMPT`'s `is_product_photo` field — so an unrelated
        canvas upload (a mood-board reference, a background texture) never fabricates a product.
        Also sets `photo_storage_ref` on the resulting row, same as the manual
        `POST /products/{id}/photo` route does, so downstream generation (`base_image_generator`'s
        image-to-image reference) can use it exactly the same way regardless of how it got there."""
        known = [
            {"id": p.id, "name": p.name, "summary": p.attributes.get("summary", "")}
            for p in existing_products
        ]
        question = (
            f"Already-known products in this session:\n{known}\n\n"
            "Decide about this uploaded image per your instructions."
        )
        try:
            # Real, live-found latency win (2026-09-29): `storage_ref` is already the real
            # asset this image_bytes came from — if it has a direct Cloudinary url, hand the
            # vision model that instead of re-encoding the bytes we already have as base64.
            result = await complete_with_vision(
                image_bytes=image_bytes, mime_type=mime_type, image_url=public_url(storage_ref),
                system=_IMAGE_SYSTEM_PROMPT, question=question, max_tokens=1024,
            )
            parsed = extract_json(result.text)
        except Exception as exc:
            log.error("product_dna_image_extraction_failed", extra={"_extra_error": str(exc)})
            raise SpecialistFailed("product_dna_service", str(exc)) from exc

        if not parsed.get("is_product_photo"):
            return None

        matched_id = parsed.get("matched_product_id")
        matched = next((p for p in existing_products if p.id == matched_id), None) if matched_id else None

        attributes = _build_attributes(
            parsed,
            price=None,
            discount_percent=None,
            description_fallback="Product photo uploaded to canvas.",
            existing=matched.attributes if matched else None,
        )

        if matched:
            matched.attributes = attributes
            matched.photo_storage_ref = storage_ref
            product = await self._products.add(matched)
        else:
            name = str(parsed.get("name") or "Unnamed product").strip()[:255]
            product = ProductProfileModel(
                id=uuid.uuid4().hex, user_id=user_id, name=name, attributes=attributes,
                photo_storage_ref=storage_ref, indexed=False,
            )
            product = await self._products.add(product)

        await get_knowledge_provider().index_document(
            collection=f"product_{user_id}", doc_id=product.id, text=_build_index_text(product.name, attributes)
        )
        product.indexed = True
        return await self._products.add(product)

    async def get_product(self, product_id: str) -> ProductProfileModel:
        product = await self._products.get(product_id)
        if product is None:
            raise NotFoundError("Product", product_id)
        return product

    async def crawl_product_from_url(
        self, *, url: str, session: SessionModel, canvas: CanvasRepository,
    ) -> tuple[ProductProfileModel, list[str]]:
        """Product crawler section (2026-09-28) — scrapes `url` (Playwright, Firecrawl on
        failure), extracts full product attributes in ONE LLM call (Ollama gemma2:2b primary,
        Groq/Replicate fallback via `get_llm_provider()`), then persists directly via
        `_build_attributes` — deliberately NOT `onboard_product`, which would fire a SECOND,
        redundant LLM extraction call over the same facts (real, live-found failure mode: that
        second call's own JSON parse failed on a real crawl during testing, needlessly, since
        `_extract_product_facts` had already produced everything `onboard_product`'s own prompt
        asks for). ALWAYS creates a new row (a fresh uuid, never an existing product_id), so a
        session can crawl as many distinct product links as it wants. Downloads up to 5
        hero/product images and lands each as a real `CanvasElementModel`, tagged with the new
        product's id/name (same construction pattern `canvas/routes.py`'s `create_element` uses)."""
        emit("crawler_started", session_id=session.id, url=url, url_type="product")
        try:
            page = await _scrape_with_fallback(url)
            facts = await _extract_product_facts(page)
            name = str(facts.get("name") or "Unnamed product").strip()[:255] or "Unnamed product"
            attributes = _build_attributes(
                facts, price=facts.get("price"), discount_percent=facts.get("discount_percent"),
                description_fallback=page.text_content,
            )
            product = ProductProfileModel(
                id=uuid.uuid4().hex, user_id=session.user_id, name=name,
                attributes=attributes, indexed=False,
            )
            product = await self._products.add(product)
            await get_knowledge_provider().index_document(
                collection=f"product_{session.user_id}", doc_id=product.id,
                text=_build_index_text(name, attributes),
            )
            product.indexed = True
            product = await self._products.add(product)

            element_ids: list[str] = []
            for image_url in page.image_urls[:_MAX_CRAWLED_IMAGES]:
                try:
                    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                        resp = await client.get(image_url)
                        resp.raise_for_status()
                        image_bytes = resp.content
                    mime_type = sniff_image_mime(image_bytes)
                    # Defense in depth alongside playwright_scraper.py's URL-based logo/icon
                    # filter — a CDN URL with no ".svg" extension can still serve SVG bytes (real,
                    # live-found case). SVG vector art (usually a logo/wordmark, never a real photo
                    # for this crawler's purpose) is never useful as a product canvas tile.
                    if mime_type == "image/svg+xml":
                        continue
                    storage_ref = save_asset(
                        image_bytes, mime_type,
                        metadata={"source": "product_crawl", "product_id": product.id, "url": image_url},
                    )
                    element = CanvasElementModel(
                        id=uuid.uuid4().hex,
                        session_id=session.id,
                        element_type="image",
                        produced_by_specialist="product_crawler",
                        version=1,
                        storage_ref=storage_ref,
                        metadata_json={"source": "product_crawl", "crawled_url": url},
                        compliance_status="disabled",  # honest "not checked", same as user_upload
                        product_id=product.id,
                        product_name=product.name,
                    )
                    created = await canvas.add_element(element)
                    element_ids.append(created.id)
                except Exception as exc:
                    log.warning("crawler_image_download_failed", extra={"_extra_url": image_url, "_extra_error": str(exc)})
                    continue

            emit("crawler_completed", session_id=session.id, url=url, url_type="product", product_profile_id=product.id)
            return product, element_ids
        except Exception as exc:
            emit("crawler_step", session_id=session.id, url=url, status="failed", error=str(exc))
            raise

    async def update_product_attributes(
        self, product_id: str, *, name: str | None = None, attributes_patch: dict[str, Any] | None = None,
    ) -> ProductProfileModel:
        """Patch semantics, not full replace — editing one field never requires resending every
        other attribute the DNA extraction already derived."""
        product = await self.get_product(product_id)
        if name is not None:
            product.name = name
        if attributes_patch:
            product.attributes = {**product.attributes, **attributes_patch}
        updated = await self._products.update(product)
        await get_knowledge_provider().index_document(
            collection=f"product_{updated.user_id}", doc_id=updated.id,
            text=_build_index_text(updated.name, updated.attributes),
        )
        return updated

    async def delete_product(self, product_id: str) -> None:
        """Does not cascade-delete this product's CanvasElementModel rows (they keep their
        product_id, now unresolved — a user can re-group/ungroup those tiles via the canvas UI's
        existing grouping controls) and does not scrub the id out of any session's
        brief.product_profile_ids (harmless — only ever read as a display list)."""
        await self.get_product(product_id)  # raises NotFoundError if it doesn't exist
        await self._products.delete(product_id)


async def reindex_all_products(products: ProductRepository) -> None:
    """
    Rehydrates LlamaIndex's in-memory Product DNA collection from the real, persisted SQL records
    on every app startup — same real gap and same fix as `brand_dna_service.reindex_all_brands()`
    (Memory.md, Phase 3). No new LLM extraction call, no cost — re-indexes attributes already
    computed and stored at onboarding time.
    """
    knowledge = get_knowledge_provider()
    for product in await products.list_all():
        if not product.indexed:
            continue
        if product.user_id:
            await knowledge.index_document(
                collection=f"product_{product.user_id}", doc_id=product.id, text=_build_index_text(product.name, product.attributes)
            )
    log.info("product_reindex_complete")
