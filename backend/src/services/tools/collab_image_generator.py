"""
Collab Image Generator tool — Fix 9 of the 2026-09-26 image/video quality investigation. A
distinct, separately-named tool (not an argument on `base_image_generator`) so the illustrator
genuinely CHOOSES between the two the same way it already chooses among its other tools — the
tool's own description below is what does the "deciding," matching this codebase's real agentic
tool-calling pattern (Environment Designer choosing `base_image_generator` vs.
`use_existing_image_as_scene`, Camera Director choosing its own motion, etc.), not a hidden code
branch or an argument the model has to remember to set correctly.

Wraps `QwenEditPlusProvider` (`qwen/qwen-image-edit-plus` via Replicate) — the only currently-wired
image model with genuine multi-reference support (`alibaba/qwen-image-3`, `base_image_generator`'s
own provider, only ever takes one reference image).
"""
from __future__ import annotations

from typing import ClassVar

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.image.qwen_edit_plus_provider import get_collab_image_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("collab_image_generator")
class CollabImageGeneratorTool(Tool):
    name = "collab_image_generator"
    description = (
        "Use this INSTEAD OF base_image_generator specifically when you need to combine two or "
        "more real, distinct visual assets into ONE generation — e.g. a brand/sponsor logo "
        "together with the product's own photo, for a named collaboration or co-branded "
        "campaign. Do NOT use this for a single-subject or purely text-described generation — "
        "use base_image_generator for that. Requires at least 2 real reference_storage_refs; "
        "each must be a real, already-relevant asset (e.g. from brand_kit_lookup's own result and "
        "the current product's own photo) — never a guessed-at or stale one."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Describes how the reference assets should be combined and the overall scene — not a description of the assets themselves.",
            },
            "reference_storage_refs": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "storage_refs of 2+ real, distinct visual assets to combine (e.g. a brand logo and a product photo).",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2"],
                "description": "Output aspect ratio. Defaults to matching the first reference image if not set.",
            },
            "seed": {
                "type": "integer",
                "description": "Set for reproducible results across calls.",
            },
        },
        "required": ["prompt", "reference_storage_refs"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, data={}, error="prompt is required")

        refs = args.get("reference_storage_refs")
        if not isinstance(refs, list) or len(refs) < 2:
            return ToolResult(
                ok=False, data={},
                error="reference_storage_refs must be a list of at least 2 real asset storage_refs",
            )

        reference_images: list[tuple[bytes, str]] = []
        for ref in refs:
            loaded = load_asset(str(ref))
            if loaded is None or not loaded[1].startswith("image/"):
                return ToolResult(
                    ok=False, data={},
                    error=f"reference_storage_refs contains a non-image or unknown asset: {ref}",
                )
            reference_images.append(loaded)

        provider = get_collab_image_provider()
        try:
            result = await provider.generate(
                prompt=prompt,
                reference_images=reference_images,
                aspect_ratio=str(args["aspect_ratio"]).strip() if args.get("aspect_ratio") else None,
                seed=int(args["seed"]) if args.get("seed") is not None else None,
            )
        except ProviderUnavailable as exc:
            return ToolResult(ok=False, data={}, error=exc.message)

        storage_ref = save_asset(
            result.image_bytes,
            result.mime_type,
            metadata={
                "prompt": prompt,
                "provider": result.provider_name,
                "reference_storage_refs": [str(r) for r in refs],
            },
        )
        return ToolResult(
            ok=True,
            data={"storage_ref": storage_ref, "mime_type": result.mime_type, "provider": result.provider_name},
        )
