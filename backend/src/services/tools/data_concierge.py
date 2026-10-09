"""
Data Concierge tool — Inter-agent natural language data retriever.
Enables specialist agents to ask factual questions about products, brand kit guidelines,
session history, or active canvas elements during execution without prompt context bloat.
"""
from __future__ import annotations

from typing import ClassVar

from ..knowledge.data_concierge_service import DataConciergeService
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("data_concierge")
class DataConciergeTool(Tool):
    name = "data_concierge"
    description = (
        "Inter-agent data assistant. Ask any question about product details, prices, discount rules, "
        "brand kit guidelines, active canvas elements, or earlier chat history context. "
        "Call this BEFORE asking the user about a missing FACT you need — it's grounded against "
        "real product/brand/canvas/session data, never invented. Returns has_data: false when "
        "nothing relevant was actually found — treat that as a genuine 'I don't have this,' not a "
        "hint to guess; only then is it appropriate to ask the user directly."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The natural language question to ask about product facts, price, brand specs, canvas assets, or prior session decisions.",
            }
        },
        "required": ["query"],
    }

    def __init__(self) -> None:
        self._service = DataConciergeService()

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        query = str(
            args.get("query")
            or args.get("question")
            or args.get("prompt")
            or args.get("search")
            or ""
        ).strip()
        if not query:
            return ToolResult(ok=False, data={}, error="query argument is required")

        ctx = context or {}
        result = await self._service.answer_query(
            query=query,
            user_id=ctx.get("user_id"),
            product_id=ctx.get("product_id"),
            session_id=ctx.get("session_id"),
            referenced_elements=ctx.get("referenced_elements_context"),
        )

        return ToolResult(
            ok=True,
            data={
                "answer": result["answer"],
                "sources": result["sources"],
                "has_data": result["has_data"],
            },
        )
