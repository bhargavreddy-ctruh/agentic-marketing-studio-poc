"""
Brand Kit Lookup tool — Architecture.md section 1b. Queries the LlamaIndex Brand DNA collection,
indexed for real by `BrandDnaService.onboard_brand()` (Phase 3). Until a brand has actually been
onboarded for a given deployment, this still correctly returns "no brand kit configured" rather
than fabricating one — specialists must handle that gracefully, not treat it as a hard failure.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.exceptions import ProviderUnavailable
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("brand_kit_lookup")
class BrandKitLookupTool(Tool):
    name = "brand_kit_lookup"
    description = "Looks up brand facts (colors, voice, logo rules) relevant to a question."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        user_id = context.get("user_id") if context else None
        if not user_id:
            return ToolResult(ok=True, data={"facts": "", "configured": False})

        # Resolve the active brand (explicit session pick wins, else first user brand)
        brand_id = context.get("brand_profile_id")
        if not brand_id:
            from ...models.base import async_session_factory
            from ...repositories.postgres.postgres_brand_repository import PostgresBrandRepository
            async with async_session_factory() as db:
                user_brands = await PostgresBrandRepository(db).list_for_user(user_id)
                if user_brands:
                    brand_id = user_brands[0].id

        if not brand_id:
            return ToolResult(ok=True, data={"facts": "", "configured": False})

        question = str(args.get("question") or "").strip()
        try:
            answer = await get_knowledge_provider().query(collection=f"brand_{brand_id}", question=question)
        except ProviderUnavailable:
            return ToolResult(ok=True, data={"facts": "", "configured": False})
        return ToolResult(ok=True, data={"facts": answer, "configured": True})
