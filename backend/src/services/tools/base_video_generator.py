"""
Base Video Generator tool — Architecture.md section 1b. Wraps whichever VideoGenProvider is
currently active.

Replicate (`prunaai/p-video`) is the active provider — real-tested end-to-end (Memory.md, Phase 2)
and the only currently-funded video credential; fal.ai stays registered as a provider file but is
not wired here (its key came back revoked on live testing). Swapping providers later is a one-line
change in this file, per Rules.md section 1/2 — no specialist or Lead file changes.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.video.replicate import get_video_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("base_video_generator")
class BaseVideoGeneratorTool(Tool):
    name = "base_video_generator"
    description = "Generates a short video clip by animating a source image with a motion prompt."
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "source_image_storage_ref": {"type": "string"},
            "aspect_ratio": {"type": "string", "default": "16:9"},
            "resolution": {"type": "string", "default": "720p"},
            "duration_seconds": {"type": "integer", "default": 5},
        },
        "required": ["prompt", "source_image_storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        source_ref = str(args.get("source_image_storage_ref") or "")
        if not prompt or not source_ref:
            return ToolResult(
                ok=False, data={}, error="prompt and source_image_storage_ref are required"
            )

        loaded = load_asset(source_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {source_ref}")
        image_bytes, _mime_type = loaded

        provider = get_video_provider()
        try:
            result = await provider.generate(
                prompt=prompt,
                image_bytes=image_bytes,
                duration_seconds=int(args.get("duration_seconds") or 5),
                aspect_ratio=str(args.get("aspect_ratio") or "16:9"),
                resolution=str(args.get("resolution") or "720p"),
            )
        except ProviderUnavailable as exc:
            return ToolResult(ok=False, data={}, error=exc.message)

        storage_ref = save_asset(
            result.video_bytes,
            result.mime_type,
            metadata={
                "prompt": prompt,
                "provider": result.provider_name,
                "source_image_storage_ref": source_ref,
            },
        )
        return ToolResult(
            ok=True,
            data={"storage_ref": storage_ref, "mime_type": result.mime_type, "provider": result.provider_name},
        )
