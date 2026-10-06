"""
Data Concierge Service — The inter-agent data assistant service.

Enables bidirectional natural language data retrieval for specialist agents (illustrator,
overlay_artist, copy_claims_checker, script_writer, etc.) on demand. Instead of bloating
individual agent prompts or repeating multi-tool lookups, any specialist can query the
DataConcierge to retrieve concise, authoritative facts on products, brand guidelines,
session history, or canvas state.
"""
from __future__ import annotations

from typing import Any

from ...core.middleware.logging import get_logger
from ...models.base import async_session_factory
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...repositories.postgres.postgres_brand_repository import PostgresBrandRepository
from ...repositories.postgres.postgres_product_repository import PostgresProductRepository
from .chat_memory_service import ChatMemoryService

log = get_logger(__name__)

_CONCIERGE_SYSTEM_PROMPT = """<role>
You are the Data Concierge for an AI Creative Marketing Studio. You are an expert data assistant answering specific questions asked by creative specialist agents (e.g. Illustrator, Overlay Artist, Copywriter, Scene Builder).
</role>

<task>
Synthesize the provided raw background facts (Product Specs, Brand Guidelines, Session History, Canvas Elements) into a precise, accurate, and concise factual answer for the asking specialist agent.
</task>

<rules>
1. **Factual Accuracy:** Only report facts present in the raw data provided. Do not guess or invent prices, colors, discounts, or specs.
2. **Concise & Direct:** Give a clear, direct answer under ~300 characters when possible so the requesting agent can immediately take creative action.
3. **Structured Specs:** If asked for prices, include currency symbols. If asked for colors, include color names or hex codes if present.
4. **Disambiguation:** If the raw data contains multiple products or variants, list them briefly so the caller knows the available options.
</rules>
"""


class DataConciergeService:
    def __init__(self) -> None:
        self._knowledge = get_knowledge_provider()
        self._chat_memory = ChatMemoryService()

    async def answer_query(
        self,
        query: str,
        *,
        user_id: str | None = None,
        product_id: str | None = None,
        session_id: str | None = None,
        referenced_elements: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Gathers context across product, brand, memory, and canvas, then returns a synthesized factual response."""
        context_chunks: list[str] = []
        sources: list[str] = []

        # 1. Product DNA Facts
        if user_id:
            async with async_session_factory() as db:
                if product_id:
                    product = await PostgresProductRepository(db).get(product_id)
                    if product:
                        context_chunks.append(
                            f"[PRODUCT STRUCTURED FACTS]\nProduct Name: {product.name}\nAttributes: {product.attributes}"
                        )
                        sources.append(f"product:{product.name}")
                else:
                    products = await PostgresProductRepository(db).list_for_user(user_id)
                    if products:
                        prod_summary = "\n".join(
                            f"- ID: {p.id}, Name: {p.name}, Specs: {p.attributes}" for p in products[:3]
                        )
                        context_chunks.append(f"[USER PRODUCTS LIST]\n{prod_summary}")
                        sources.append("products_list")

            try:
                if product_id:
                    rag_facts = await self._knowledge.query_document(
                        collection=f"product_{user_id}", doc_id=product_id, question=query
                    )
                else:
                    rag_facts = await self._knowledge.query(collection=f"product_{user_id}", question=query)
                if rag_facts:
                    context_chunks.append(f"[PRODUCT RAG FACTS]\n{rag_facts}")
                    sources.append("product_rag")
            except Exception as exc:
                # Optional enrichment, not required — an honest "skip it" rather than failing the
                # whole concierge answer over one of several context sources being unavailable.
                log.debug("data_concierge_product_rag_failed", extra={"_extra_error": str(exc)})

        # 2. Brand Guidelines
        if user_id:
            async with async_session_factory() as db:
                brands = await PostgresBrandRepository(db).list_for_user(user_id)
                brand = brands[0] if brands else None
                if brand:
                    context_chunks.append(f"[BRAND IDENTITY]\nName: {brand.name}\nGuidelines: {brand.guidelines}")
                    sources.append(f"brand:{brand.name}")

            try:
                brand_rag = await self._knowledge.query(collection=f"brand_{user_id}", question=query)
                if brand_rag:
                    context_chunks.append(f"[BRAND RAG GUIDELINES]\n{brand_rag}")
                    sources.append("brand_rag")
            except Exception as exc:
                log.debug("data_concierge_brand_rag_failed", extra={"_extra_error": str(exc)})

        # 3. Session / Chat Memory
        if session_id:
            try:
                history_text = await self._chat_memory.get_relevant_history(session_id, query)
                if history_text:
                    context_chunks.append(f"[RELEVANT SESSION HISTORY]\n{history_text}")
                    sources.append("session_memory")
            except Exception as exc:
                log.debug("data_concierge_session_memory_failed", extra={"_extra_error": str(exc)})

        # 4. Canvas Referenced Elements
        if referenced_elements:
            elem_summary = "\n".join(
                f"- Type: {el.get('element_type')}, Description: {el.get('description')}, StorageRef: {el.get('storage_ref')}"
                for el in referenced_elements
            )
            context_chunks.append(f"[CANVAS ELEMENTS]\n{elem_summary}")
            sources.append("canvas_elements")

        if not context_chunks:
            return {
                "answer": "No specific product, brand, or session facts found matching your query.",
                "sources": [],
                "has_data": False,
            }

        # 5. Synthesize answer with Tier-1 fast model
        full_context = "\n\n".join(context_chunks)
        llm = get_llm_provider()
        user_prompt = f"Agent Query: {query}\n\nAvailable Knowledge:\n{full_context}"

        try:
            res = await llm.complete(
                tier=ModelTier.TIER_1,
                system=_CONCIERGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=512,
            )
            answer = (res.text or "").strip()
        except Exception as e:
            log.warning("data_concierge_synthesis_failed", extra={"_extra_error": str(e)})
            answer = f"Raw facts gathered:\n{full_context[:400]}"

        return {
            "answer": answer,
            "sources": sources,
            "has_data": True,
        }
