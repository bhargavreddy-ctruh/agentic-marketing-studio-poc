"""
Recall tool (2026-10-05, explicit user design: "recall(query | artifactId) returns exact history
on demand"). Replaces `session_service.py`'s old unconditional `ChatMemoryService.get_relevant_
history()` call (fired on every single turn, whether or not anything needed recalling) with a real
tool a specialist calls only when the Ledger/Window/Digests it already has don't answer the
question — keyword search first (cheap, exact, no index to go stale), the existing LlamaIndex
semantic search only as a fallback when keyword search comes up empty.
"""
from __future__ import annotations

from typing import ClassVar

from ...models.base import async_session_factory
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("recall")
class RecallTool(Tool):
    name = "recall"
    description = (
        "Looks up exact history from earlier in this session — a specific past decision, number, "
        "or detail that isn't already in your current context. Pass a free-text `query`, or an "
        "`artifact_id` to find turns that mention a specific canvas element."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to recall, in plain words."},
            "artifact_id": {"type": "string", "description": "A specific canvas element id, if recalling about one."},
        },
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        session_id = context.get("session_id") if context else None
        if not session_id:
            return ToolResult(ok=True, data={"turns": [], "note": "no session context available"})

        query = str(args.get("query") or "").strip()
        artifact_id = str(args.get("artifact_id") or "").strip()
        search_text = query or artifact_id
        if not search_text:
            return ToolResult(ok=True, data={"turns": [], "note": "no query or artifact_id given"})

        from ..knowledge.chat_memory_service import ChatMemoryService

        chat_memory = ChatMemoryService()
        async with async_session_factory() as db:
            keyword_hits = await chat_memory.keyword_search(db, session_id, search_text)

        if keyword_hits:
            return ToolResult(
                ok=True,
                data={
                    "source": "keyword",
                    "turns": [
                        {"user": t.user_text, "assistant": t.assistant_text or ""} for t in keyword_hits
                    ],
                },
            )

        # Keyword search came up empty — fall back to the existing semantic index, same real
        # behavior `session_service.py` used to run unconditionally, now only reached on demand.
        semantic = await chat_memory.get_relevant_history(session_id, search_text)
        return ToolResult(ok=True, data={"source": "semantic", "turns": [], "summary": semantic})
