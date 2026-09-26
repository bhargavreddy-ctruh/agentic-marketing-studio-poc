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
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
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
        try:
            answer = await get_knowledge_provider().query(collection=f"product_{user_id}", question=question)
        except ProviderUnavailable:
            return ToolResult(ok=True, data={"facts": "", "configured": False})
        return ToolResult(ok=True, data={"facts": answer, "configured": True})
