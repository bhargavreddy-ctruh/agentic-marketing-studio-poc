"""
Image Editor/Inpainter tool — Architecture.md section 1b. Used by both Illustrator
(self-refinement) and Composition Artist.

Tries Cloudflare's FLUX.2 [klein] 4B first, falling back to HuggingFace only if Cloudflare isn't
configured or a real call to it fails (2026-09-21: HuggingFace's fal-ai sub-provider has a real,
disclosed 402 Payment Required — Cloudflare's free Neuron allocation is a genuine, not
hypothetical, second option). Both are behind the same `ImageEditProvider` Protocol, so this file
never imports a vendor SDK directly (Rules.md section 1).
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...core.middleware.logging import get_logger
from ...providers.image.cloudflare_flux import get_cloudflare_flux_provider as get_image_edit_provider
from .base import Tool, ToolResult
from .registry import register_tool

log = get_logger(__name__)

@register_tool("image_editor")
class ImageEditorTool(Tool):
    name = "image_editor"
    description = "Applies a targeted edit to an existing image, given its storage_ref."
    input_schema = {
        "type": "object",
        "properties": {
            "storage_ref": {"type": "string"},
            "instruction": {"type": "string"},
        },
        "required": ["storage_ref", "instruction"],
    }

    async def run(self, args: dict) -> ToolResult:
        storage_ref = str(args.get("storage_ref") or "")
        instruction = str(args.get("instruction") or "").strip()
        if not storage_ref or not instruction:
            return ToolResult(ok=False, data={}, error="storage_ref and instruction are required")

        loaded = load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        image_bytes, mime_type = loaded

        try:
            result = await get_image_edit_provider().edit(
                image_bytes=image_bytes, mime_type=mime_type, instruction=instruction
            )
        except ProviderUnavailable as exc:
            return ToolResult(ok=False, data={}, error=f"{exc.message}")

        new_ref = save_asset(
            result.image_bytes,
            result.mime_type,
            metadata={"instruction": instruction, "provider": result.provider_name, "edited_from": storage_ref},
        )
        return ToolResult(
            ok=True,
            data={"storage_ref": new_ref, "mime_type": result.mime_type, "provider": result.provider_name},
        )
