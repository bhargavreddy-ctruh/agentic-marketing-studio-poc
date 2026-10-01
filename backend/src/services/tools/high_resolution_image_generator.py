"""
High-Resolution Image Generator tool — explicit user ask (2026-10-01): "use this to generate 2k
4k... should be called based on context and only if user asks 2k high resolution etc."

A distinct, separately-named tool (same real agentic-choice pattern as `collab_image_generator.py`
and `photorealistic_image_generator.py`) — gated specifically on EXPLICIT resolution/fidelity
requests, never a default. Wraps Google's Nano Banana 2 (`providers/image/nano_banana_2_provider.py`),
the higher-fidelity, higher-resolution sibling of Nano Banana 2 Lite — slower and more expensive,
so only worth reaching for when the request genuinely calls for it.

Real web grounding — confirmed against the real OpenAPI schema: `google_search` (real-time
information — weather, sports scores, recent events) and `image_search` (real web images as
visual context, also auto-enables web search). Both are real, both default off — only set one
when the request genuinely needs current/real-world context, never by default.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.image.nano_banana_2_provider import get_nano_banana_2_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("high_resolution_image_generator")
class HighResolutionImageGeneratorTool(Tool):
    name = "high_resolution_image_generator"
    description = (
        "Use this ONLY when the user explicitly asks for high resolution, 2K, 4K output, or "
        "otherwise clearly needs the highest achievable image fidelity. Slower and more expensive "
        "than the default tools — NEVER use this as a default; for a normal request use "
        "base_image_generator, and for a photorealistic-but-not-explicitly-high-res request use "
        "photorealistic_image_generator instead. Works with zero reference images (pure "
        "text-to-image) or with reference_storage_refs (1-14 real, distinct visual assets) to edit "
        "or combine them at high resolution."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "reference_storage_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: 1-14 real, distinct visual assets to edit or combine at high resolution. Omit entirely for pure text-to-image.",
            },
            "resolution": {
                "type": "string",
                "enum": ["512px", "1K", "2K", "4K"],
                "default": "2K",
                "description": "Output resolution — set to match what the user explicitly asked for (e.g. '4K' if they said 4K).",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "match_input_image"],
                "default": "match_input_image",
                "description": "Output aspect ratio — defaults to matching a reference image's own aspect ratio (the real API's own default); set an explicit ratio for pure text-to-image or to override.",
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
            "google_search": {
                "type": "boolean",
                "default": False,
                "description": "Ground generation in real-time information (weather, sports scores, recent events) via Google Web Search. Only set true when the request genuinely needs current/real-world factual context.",
            },
            "image_search": {
                "type": "boolean",
                "default": False,
                "description": "Ground generation using real web images (via Google Image Search) as visual context — also auto-enables web search. Only set true when the request genuinely needs to be grounded in what something real currently/actually looks like.",
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

        provider = get_nano_banana_2_provider()
        try:
            result = await provider.generate(
                prompt=prompt,
                reference_images=reference_images or None,
                aspect_ratio=str(args["aspect_ratio"]).strip() if args.get("aspect_ratio") else None,
                resolution=str(args.get("resolution") or "2K"),
                output_format=str(args.get("output_format") or "jpg"),
                seed=int(args["seed"]) if args.get("seed") is not None else None,
                google_search=bool(args.get("google_search", False)),
                image_search=bool(args.get("image_search", False)),
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
