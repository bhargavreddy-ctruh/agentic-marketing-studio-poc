"""
Text Card Writer tool (2026-09-22) — a real, modular tool, exactly like every other one here
(`base_image_generator`, `text_to_speech`, ...): a specialist's own LLM genuinely decides whether
to call it, with what content, not Python code hand-building a text string after the fact.

Added per an explicit user ask: text content (a creative brief, a shot list, a scene description,
or a description of an existing canvas element) should be a real tool a specialist calls — the
same agentic pattern as every other generation capability — not something bolted onto a Lead's own
Python code as a special case. Writes the text to disk via the same `save_asset`/`storage_ref`
mechanism every other tool already uses (as real `text/plain` bytes) — so a text card is a genuine
canvas element like any other, not a special-cased shape; `GET /api/v1/canvas/assets/{storage_ref}`
serves it exactly like an image or audio asset would.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.local_storage import save_asset
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("text_card_writer")
class TextCardWriterTool(Tool):
    name = "text_card_writer"
    description = (
        "Writes a real text card onto the canvas — a creative brief, a shot list, a scene "
        "description, a narrative summary, or a written description of an existing image/video/"
        "audio element. Use whenever the user explicitly asks for something to be described, "
        "summarized, or narrated in writing, or when documenting the real creative reasoning "
        "behind a generation as its own visible card."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Short label for the card, e.g. 'creative_brief', 'shot_list', 'scene_description', 'element_description'",
            },
            "text": {"type": "string", "description": "The actual real text content to show on the card"},
        },
        "required": ["label", "text"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        label = str(args.get("label") or "note").strip() or "note"
        text = str(args.get("text") or "").strip()
        if not text:
            return ToolResult(ok=False, data={}, error="text is required")

        storage_ref = await save_asset(text.encode("utf-8"), "text/plain", metadata={"label": label})
        return ToolResult(ok=True, data={"storage_ref": storage_ref, "mime_type": "text/plain", "label": label})
