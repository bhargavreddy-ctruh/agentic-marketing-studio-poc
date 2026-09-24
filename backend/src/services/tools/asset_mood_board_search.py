"""
Asset/Mood Board Search tool — Architecture.md section 1b. Wraps the real internal asset library
(`services/knowledge/mood_board_service.py`) that now exists — `POST /api/v1/mood-board/assets`
lets a real prior-campaign asset be uploaded, indexed, and found here.

Tools run inside `run_specialist_agentic`, deep in a LangGraph node with no FastAPI request/DB
session available (unlike route handlers, which get one via `api/dependencies.py`'s DI). So this
opens its own short-lived session directly from the same `async_session_factory` the DI layer
itself wraps — the only DB access any tool in this codebase needs, and still routed through the
real `MoodBoardRepository`/`MoodBoardService`, never raw SQL here.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...models.base import async_session_factory
from ...repositories.sqlite.sqlite_mood_board_repository import SqliteMoodBoardRepository
from ...services.knowledge.mood_board_service import MoodBoardService
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("asset_mood_board_search")
class AssetMoodBoardSearchTool(Tool):
    name = "asset_mood_board_search"
    description = "Searches internal prior-campaign assets for mood-board references."
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, data={}, error="query is required")

        async with async_session_factory() as db:
            service = MoodBoardService(assets=SqliteMoodBoardRepository(db))
            try:
                assets = await service.search(query=query)
            except ProviderUnavailable:
                # No assets uploaded yet — the same honest "not configured" shape the stub always
                # returned, not an error (real precedent: brand_kit_lookup does the same before
                # any brand is onboarded).
                return ToolResult(ok=True, data={"results": [], "configured": False})

        return ToolResult(
            ok=True,
            data={
                "results": [
                    {"storage_ref": a.storage_ref, "mime_type": a.mime_type, "description": a.description}
                    for a in assets
                ],
                "configured": True,
            },
        )
