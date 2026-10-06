"""
Product Lookup tool — Architecture.md section 1b. Queries the LlamaIndex Product DNA collection
(indexed for real by `ProductDnaService.onboard_product()`, Phase 3). Used by Illustrator, Overlay
Artist, and the Compliance checkers to bind exact product facts (must-show/never-show/claims/price)
rather than letting a model free-generate them — the guardrail-first pattern (Architecture.md
section 1).
"""
from __future__ import annotations

from typing import ClassVar

from ...core.exceptions import ProviderUnavailable
from ...models.base import async_session_factory
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...repositories.postgres.postgres_product_repository import PostgresProductRepository
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("product_lookup")
class ProductLookupTool(Tool):
    name = "product_lookup"
    description = "Looks up product facts (must-show, never-show, allowed claims, price) relevant to a question."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        user_id = context.get("user_id") if context else None
        if not user_id:
            return ToolResult(ok=True, data={"facts": "", "configured": False})

        question = str(args.get("question") or "").strip()
        # Real, live-found bug (2026-09-30): an unscoped semantic search over the user's WHOLE
        # product collection is guaranteed to surface whichever product a fact happened to be
        # indexed under, not necessarily the one actually relevant this turn — a session with
        # several linked products got a DIFFERENT product's facts back. Scopes to the turn's
        # resolved product when one is known (threaded through by session_service.py/runner.py);
        # falls back to the prior unscoped behavior when none resolved, unchanged.
        product_id = context.get("product_id") if context else None

        # Real, live-found gap (2026-10-05): this tool's own docstring promises "exact facts...
        # rather than letting a model free-generate them" (the guardrail-first pattern), but it
        # only ever returned a RAG-summarized free-text answer — lossy by construction, and a real
        # cause of a specialist's output missing facts that genuinely exist (the vector-retrieval
        # question/answer pass can miss or paraphrase a specific field like price or a must_show
        # item). When `product_id` is known, fetch the REAL structured attributes row directly and
        # return it verbatim alongside the RAG answer, so the caller sees exact must_show/
        # never_show/claims_allowed/claims_disallowed/price/currency, not just a paraphrase.
        attributes: dict | None = None
        if product_id:
            async with async_session_factory() as db:
                product = await PostgresProductRepository(db).get(product_id)
            if product is not None:
                attributes = product.attributes

        try:
            knowledge = get_knowledge_provider()
            if product_id:
                answer = await knowledge.query_document(
                    collection=f"product_{user_id}", doc_id=product_id, question=question
                )
            else:
                answer = await knowledge.query(collection=f"product_{user_id}", question=question)
        except ProviderUnavailable:
            answer = ""

        if attributes is None and not answer:
            return ToolResult(ok=True, data={"facts": "", "configured": False})
        return ToolResult(
            ok=True,
            data={"facts": answer, "configured": True, "attributes": attributes or {}},
        )
