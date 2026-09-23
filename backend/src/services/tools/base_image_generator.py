"""
Base Image Generator tool — Architecture.md section 1b. Wraps whichever ImageGenProvider is
currently active (Pollinations today; Gemini/Vertex/Bedrock once quota returns — Rules.md
section 6). The specialist calling this never knows which provider actually ran.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import save_asset
from ...providers.image.replicate_provider import get_image_gen_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("base_image_generator")
class BaseImageGeneratorTool(Tool):
    name = "base_image_generator"
    description = "Generates a still image from a text prompt."
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "aspect_ratio": {"type": "string", "default": "1:1"},
        },
        "required": ["prompt"],
    }

    async def run(self, args: dict) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, data={}, error="prompt is required")

        provider = get_image_gen_provider()
        try:
            result = await provider.generate(
                prompt=prompt, aspect_ratio=str(args.get("aspect_ratio") or "1:1")
            )
        except ProviderUnavailable as exc:
            return ToolResult(ok=False, data={}, error=exc.message)

        storage_ref = save_asset(
            result.image_bytes,
            result.mime_type,
            metadata={"prompt": prompt, "provider": result.provider_name},
        )
        return ToolResult(
            ok=True,
            data={"storage_ref": storage_ref, "mime_type": result.mime_type, "provider": result.provider_name},
        )
