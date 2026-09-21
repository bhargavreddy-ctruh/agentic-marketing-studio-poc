"""
Product Attribute Extractor / Product DNA Agent — Architecture.md section 2.2, section 6: a
near-direct port of the existing agentic_flow codebase's `run_product_intelligence`
(`hitl_agents.py`), which already produces the exact `mustShow`/`neverShow`/`claimsAllowed`/
`claimsDisallowed`/`labelVisibility` shape the guardrail-first pattern needs (Rules.md section 6).

Text-only for this POC (no product-photo upload endpoint exists yet) — the original also accepts
reference photos; that's a real gap versus the full port, not hidden: `description` is all this
version has to work from.
"""
from __future__ import annotations

import uuid
from typing import Any

from ...core.exceptions import NotFoundError, SpecialistFailed
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...models.product_profile import ProductProfileModel
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...repositories.base import ProductRepository

log = get_logger(__name__)


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


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()][:12]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


class ProductDnaService:
    def __init__(self, products: ProductRepository):
        self._products = products

    async def onboard_product(
        self, *, name: str, description: str, price: float | None, discount_percent: float | None
    ) -> ProductProfileModel:
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

        attributes = {
            "summary": str(parsed.get("summary") or description[:400]),
            "price": price,
            "discount_percent": discount_percent,
            "must_show": _str_list(parsed.get("must_show")),
            "never_show": _str_list(parsed.get("never_show")),
            "claims_allowed": _str_list(parsed.get("claims_allowed")),
            "claims_disallowed": _str_list(parsed.get("claims_disallowed")),
            "label_visibility": str(parsed.get("label_visibility") or ""),
        }

        product = ProductProfileModel(
            id=uuid.uuid4().hex, name=name, attributes=attributes, indexed=False
        )
        product = await self._products.add(product)

        await get_knowledge_provider().index_document(
            collection="product", doc_id=product.id, text=_build_index_text(name, attributes)
        )
        product.indexed = True
        return await self._products.add(product)

    async def get_product(self, product_id: str) -> ProductProfileModel:
        product = await self._products.get(product_id)
        if product is None:
            raise NotFoundError("Product", product_id)
        return product


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
        await knowledge.index_document(
            collection="product", doc_id=product.id, text=_build_index_text(product.name, product.attributes)
        )
    log.info("product_reindex_complete")
