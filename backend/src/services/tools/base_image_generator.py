"""
Base Image Generator tool — Architecture.md section 1b. Wraps whichever ImageGenProvider is
currently active (`alibaba/qwen-image-3` via Replicate today — see `replicate_provider.py`;
Gemini/Vertex/Bedrock/Pollinations provider files exist but are unwired). The specialist calling
this never knows which provider actually ran.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.element_descriptions import is_real_description
from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.image.replicate_provider import get_image_gen_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("base_image_generator")
class BaseImageGeneratorTool(Tool):
    name = "base_image_generator"
    description = "Generates a still image from a text prompt. Pass reference_storage_ref when a referenced element exists and the new image should be visually grounded in it (image-to-image), instead of only describing it in the prompt."
    input_schema: ClassVar[dict] = {
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

        # 1. Image-to-image reference (explicit arg takes precedence over context product photo).
        # Real, live-found gap (2026-09-25, same root cause as the palette tools' fix this
        # session): trying only the FIRST non-empty candidate meant a bad/hallucinated explicit
        # `reference_storage_ref` from the model silently degraded generation to text-only, even
        # when a correct context value was right there. Now tries each real candidate in
        # precedence order until one actually resolves to a real image, instead of stopping at the
        # first non-empty string regardless of whether it's real.
        reference_image_bytes: bytes | None = None
        reference_mime_type: str | None = None
        reference_storage_ref = ""
        for candidate in (
            str(args.get("reference_storage_ref") or "").strip(),
            str(ctx.get("product_photo_storage_ref") or "").strip(),
            str(ctx.get("reference_storage_ref") or "").strip(),
        ):
            if not candidate:
                continue
            loaded_ref = await load_asset(candidate)
            if loaded_ref is not None and loaded_ref[1] and loaded_ref[1].startswith("image/"):
                reference_storage_ref = candidate
                reference_image_bytes, reference_mime_type = loaded_ref
                break
            # A non-empty candidate that didn't resolve (or resolved to a non-image, e.g. video)
            # is remembered only so the prompt-injection step below (which needs SOME ref string
            # to match against referenced_elements_context) still has a value if every candidate
            # fails — but the loop keeps trying real image candidates first.
            reference_storage_ref = reference_storage_ref or candidate

        # 2. Campaign style reference (explicit arg takes precedence over context session style lock)
        style_reference_bytes: bytes | None = None
        style_reference_mime_type: str | None = None
        style_ref_storage = (
            str(args.get("style_reference_storage_ref") or "").strip()
            or str(ctx.get("style_ref_storage_ref") or "").strip()
        )
        if style_ref_storage:
            loaded_style = await load_asset(style_ref_storage)
            if loaded_style is not None:
                if loaded_style[1] and loaded_style[1].startswith("image/"):
                    style_reference_bytes, style_reference_mime_type = loaded_style
                else:
                    # Ignore non-image style references
                    pass

        # 3. Seed fallback from context style_seed if not explicitly given
        seed = int(args["seed"]) if args.get("seed") is not None else ctx.get("style_seed")
        
        # 4. Inject visual description into prompt so the image-to-image reference is grounded in
        # words too, not just pixels.
        # Real, live-found bug (2026-09-26): `el.get("description")` used to be treated as real
        # descriptive text whenever it was merely non-empty — but `session_service.py` fills a
        # placeholder sentinel (`NO_DESCRIPTION_SENTINEL`) when no real description was ever
        # captured, and that placeholder was getting appended VERBATIM into the actual prompt sent
        # to the image model: "...Visually ground the generation in this reference: (no
        # description recorded)." An image model has no way to know that's a placeholder — it
        # renders it as literal instruction. `is_real_description` rejects the sentinel (and any
        # other empty/falsy value), so a missing description now correctly means "skip this append
        # entirely," not "append meaningless text."
        if reference_storage_ref:
            elements = ctx.get("referenced_elements_context") or []
            for el in elements:
                desc = el.get("description")
                if el.get("storage_ref") == reference_storage_ref and is_real_description(desc):
                    if desc.lower() not in prompt.lower():
                        prompt = f"{prompt}. Visually ground the generation in this reference: {desc}"
                    break

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

        storage_ref = await save_asset(
            result.image_bytes,
            result.mime_type,
            metadata={"prompt": prompt, "provider": result.provider_name},
        )
        return ToolResult(
            ok=True,
            data={"storage_ref": storage_ref, "mime_type": result.mime_type, "provider": result.provider_name},
        )
