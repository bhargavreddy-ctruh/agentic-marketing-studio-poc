"""
Base Image Generator tool — Architecture.md section 1b. Wraps whichever ImageGenProvider is
currently active (Pollinations today; Gemini/Vertex/Bedrock once quota returns — Rules.md
section 6). The specialist calling this never knows which provider actually ran.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.image.pollinations import get_image_gen_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("base_image_generator")
class BaseImageGeneratorTool(Tool):
    name = "base_image_generator"
    description = "Generates a still image from a text prompt. Pass reference_storage_ref when a referenced element exists and the new image should be visually grounded in it (image-to-image), instead of only describing it in the prompt."
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2"],
                "default": "1:1",
                "description": "Output aspect ratio.",
            },
            "negative_prompt": {
                "type": "string",
                "description": "Elements to avoid in the generated image.",
            },
            "enable_prompt_expansion": {
                "type": "boolean",
                "default": True,
                "description": "Automatically expand and optimize the prompt.",
            },
            "seed": {
                "type": "integer",
                "description": "Set for reproducible results across calls.",
            },
            # Real, live-found gap (2026-09-24, per an explicit user ask: "image generation model
            # accepts image reference for generation, why are we not using that and its asking for
            # image to text then text to image"): Qwen genuinely supports a real `image` input for
            # image-to-image generation, not just editing — but this tool had no way to pass one,
            # so a referenced element only ever reached generation as a text description
            # (`session_service.py`'s `_describe_uploaded_image`), never the real pixels. Wired
            # through now — real, not a text-only proxy.
            "style_reference_storage_ref": {
                "type": "string",
                "description": "storage_ref of a campaign style reference image to guide visual aesthetic.",
            },
            "reference_storage_ref": {
                "type": "string",
                "description": "storage_ref of an existing image to visually ground this generation in (image-to-image), when one is available and relevant.",
            },
            "width": {
                "type": "integer",
                "description": "Explicit output width in pixels.",
            },
            "height": {
                "type": "integer",
                "description": "Explicit output height in pixels.",
            },
        },
        "required": ["prompt"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, data={}, error="prompt is required")

        ctx = context or {}

        # 1. Image-to-image reference (explicit arg takes precedence over context product photo)
        reference_image_bytes: bytes | None = None
        reference_mime_type: str | None = None
        reference_storage_ref = (
            str(args.get("reference_storage_ref") or "").strip()
            or str(ctx.get("product_photo_storage_ref") or "").strip()
            or str(ctx.get("reference_storage_ref") or "").strip()
        )
        if reference_storage_ref:
            loaded_ref = load_asset(reference_storage_ref)
            if loaded_ref is not None:
                reference_image_bytes, reference_mime_type = loaded_ref

        # 2. Campaign style reference (explicit arg takes precedence over context session style lock)
        style_reference_bytes: bytes | None = None
        style_reference_mime_type: str | None = None
        style_ref_storage = (
            str(args.get("style_reference_storage_ref") or "").strip()
            or str(ctx.get("style_ref_storage_ref") or "").strip()
        )
        if style_ref_storage:
            loaded_style = load_asset(style_ref_storage)
            if loaded_style is not None:
                style_reference_bytes, style_reference_mime_type = loaded_style

        # 3. Seed fallback from context style_seed if not explicitly given
        seed = int(args["seed"]) if args.get("seed") is not None else ctx.get("style_seed")

        provider = get_image_gen_provider()
        try:
            result = await provider.generate(
                prompt=prompt,
                aspect_ratio=str(args.get("aspect_ratio") or "1:1"),
                negative_prompt=str(args["negative_prompt"]).strip() or None if args.get("negative_prompt") else None,
                enable_prompt_expansion=bool(args.get("enable_prompt_expansion", True)),
                seed=seed,
                reference_image_bytes=reference_image_bytes,
                reference_mime_type=reference_mime_type,
                style_reference_bytes=style_reference_bytes,
                style_reference_mime_type=style_reference_mime_type,
                width=int(args["width"]) if args.get("width") is not None else None,
                height=int(args["height"]) if args.get("height") is not None else None,
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
