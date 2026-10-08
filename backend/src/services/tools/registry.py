"""
TOOL_REGISTRY — the single lookup point every specialist uses to find a tool by name.

Adding tool #20: write services/tools/<new_tool>.py implementing Tool, decorate its class with
@register_tool("its_name"), import that module once in load_all_tools() below. That's the whole
change — no orchestrator, Lead, or specialist file is ever touched (Rules.md section 1/2).
"""
from __future__ import annotations

from ...core.exceptions import ToolNotFound
from ...core.middleware.logging import get_logger
from .base import Tool

log = get_logger(__name__)

TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(name: str):
    def _decorator(cls):
        instance = cls()
        instance.name = name
        TOOL_REGISTRY[name] = instance
        log.info("tool_registered", extra={"_extra_tool": name})
        return cls

    return _decorator


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise ToolNotFound(name)
    return tool


def to_openai_tool_schema(tool: Tool) -> dict:
    """Converts a Tool into the OpenAI-compatible function-calling schema OpenRouter/Groq expect
    in a `tools=[...]` request — the seam that lets a specialist's own LLM call genuinely decide
    whether/which/how to invoke a tool at runtime, instead of Python code deciding for it
    (Memory.md, Phase 2: the move away from "LLM decides in text, code calls the tool")."""
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema},
    }


def load_all_tools() -> None:
    """
    Import every tool module once, so its @register_tool decorator runs. Growing one line per
    tool as later phases build them — Architecture.md section 1b's 19 tools. Adding a line here
    is the only "central" edit a new tool ever needs, and it's an addition, never a modification
    of existing code.
    """
    from . import (  # noqa: F401
        asset_mood_board_search,
        audio_transcriber,
        audio_video_muxer,
        base_image_generator,
        base_video_generator,
        brand_kit_lookup,
        collab_image_generator,
        color_palette_extractor,
        data_concierge,
        delegate_task,
        discount_claims_calculator,
        discount_math_calculator,
        export_asset,
        high_resolution_image_generator,
        image_crop_resize,
        image_editor,
        logo_compositor,
        photorealistic_image_generator,
        product_lookup,
        recall,
        text_card_writer,
        text_overlay,
        text_to_speech,
        video_stitcher,
        visual_palette_analyzer,
        web_trend_search,
    )

    log.info("tools_loaded", extra={"_extra_count": len(TOOL_REGISTRY)})
