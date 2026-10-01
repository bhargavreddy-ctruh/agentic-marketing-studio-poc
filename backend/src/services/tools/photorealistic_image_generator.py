"""
Photorealistic Image Generator tool — explicit user ask (2026-10-01): "add nano banana fast from
replicate as the tool to generate photorealistic images... connect it based on context, models/llm
should call this."

A distinct, separately-named tool (not a hidden flag on `base_image_generator`) so the specialist
genuinely CHOOSES it based on context — the same real agentic-choice pattern
`collab_image_generator.py` already established for its own distinct case (its own docstring:
"a distinct, separately-named tool... so the illustrator genuinely CHOOSES between the two the
same way it already chooses among its other tools"). This tool's own `description` is what does
the choosing: a request that genuinely calls for a photorealistic result (a real-world realistic
photo/product shot, not a stylized illustration) should reach for THIS tool instead of
`base_image_generator`/`collab_image_generator`.

Wraps Google's Nano Banana 2 Lite (`providers/image/nano_banana_provider.py`) — fast, low-cost,
and genuinely flexible: works with zero reference images (pure text-to-image) or up to 14 real
ones (editing/combining), so one tool covers both the "generate from scratch" and "combine real
assets" cases for photorealistic requests specifically, rather than needing two separate tools the
way the non-photorealistic path does.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.image.nano_banana_provider import get_nano_banana_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("photorealistic_image_generator")
class PhotorealisticImageGeneratorTool(Tool):
    name = "photorealistic_image_generator"
    description = (
        "Use this INSTEAD OF base_image_generator/collab_image_generator specifically when the "
        "request calls for a genuinely PHOTOREALISTIC result — a real-world realistic photo or "
        "product shot, not a stylized/illustrated/artistic look. Fast and low-cost (Google's Nano "
        "Banana 2 Lite). Works with zero reference images (pure text-to-image) or with "
        "reference_storage_refs (1-14 real, distinct visual assets) to edit or combine them into "
        "one photorealistic scene. Do NOT use this for a stylized/illustrated/artistic request — "
        "use base_image_generator for that."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "reference_storage_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: 1-14 real, distinct visual assets (e.g. a product photo, a brand logo) to edit or combine into the photorealistic result. Omit entirely for pure text-to-image.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": [
                    "1:1", "1:4", "1:8", "2:3", "3:2", "3:4", "4:1", "4:3", "4:5", "5:4",
                    "8:1", "9:16", "16:9", "21:9", "match_input_image",
                ],
                "default": "1:1",
                "description": "Output aspect ratio. Use 'match_input_image' to automatically match a reference image's own aspect ratio.",
            },
            "output_format": {
                "type": "string",
                "enum": ["jpg", "png"],
                "default": "jpg",
            },
            "seed": {
                "type": "integer",
                "description": "Set for reproducible results across calls.",
            },
        },
        "required": ["prompt"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, data={}, error="prompt is required")

        refs = args.get("reference_storage_refs") or []
        if not isinstance(refs, list):
            return ToolResult(ok=False, data={}, error="reference_storage_refs must be a list of storage_refs")
        if len(refs) > 14:
            return ToolResult(ok=False, data={}, error="reference_storage_refs supports at most 14 real assets")

        reference_images: list[tuple[bytes, str]] = []
        for ref in refs:
            loaded = await load_asset(str(ref))
            if loaded is None or not loaded[1].startswith("image/"):
                return ToolResult(
                    ok=False, data={},
                    error=f"reference_storage_refs contains a non-image or unknown asset: {ref}",
                )
            reference_images.append(loaded)

        provider = get_nano_banana_provider()
        try:
            result = await provider.generate(
                prompt=prompt,
                reference_images=reference_images or None,
                aspect_ratio=str(args["aspect_ratio"]).strip() if args.get("aspect_ratio") else None,
                output_format=str(args.get("output_format") or "jpg"),
                seed=int(args["seed"]) if args.get("seed") is not None else None,
            )
        except ProviderUnavailable as exc:
            return ToolResult(ok=False, data={}, error=exc.message)

        storage_ref = await save_asset(
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
