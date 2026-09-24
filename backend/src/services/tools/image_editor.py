"""
Image Editor/Inpainter tool — Architecture.md section 1b. Used by both Illustrator
(self-refinement) and Composition Artist.

Switched to `alibaba/qwen-image-3` (Replicate) 2026-09-24, per an explicit user ask to verify every
real input `readme.md` documents for this model was actually reaching it, and a real user report of
"very bad output" on a real edit. Was previously wired to Cloudflare FLUX.2 [klein] 4B
(`cloudflare_flux.py`) — a real, disclosed problem, not a style preference: FLUX.2 never accepted
`aspect_ratio` at all (the provider's own code admits this — an unverified field it silently drops),
had no `negative_prompt`/`seed`/`enable_prompt_expansion` support, AND hard-downscaled every input
image to under 512x512 before editing — real quality loss for a normal canvas-sized asset. This
file's own docstring also used to claim a Cloudflare-then-HuggingFace fallback that never actually
existed in the code — stale, not real behavior. Qwen genuinely supports the full parameter set
`readme.md` documents (see `providers/image/replicate_provider.py`), at a real, disclosed cost:
$0.03/edit on Replicate (Cloudflare's allocation was effectively free) — an explicit tradeoff the
user chose after being shown both options, not a silent cost increase.
"""
from __future__ import annotations

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...core.middleware.logging import get_logger
from ...providers.image.replicate_provider import get_image_edit_provider
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
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2"],
                "description": (
                    "Set this ONLY when the edit should change the output's shape (e.g. "
                    "\"make this a 9:16 Instagram post\"). Leave unset otherwise — the edit keeps "
                    "the input image's own aspect ratio by default."
                ),
            },
            "negative_prompt": {
                "type": "string",
                "description": "Elements to avoid in the edited image.",
            },
            "enable_prompt_expansion": {
                "type": "boolean",
                "default": True,
                "description": "Automatically expand and optimize the instruction.",
            },
            "seed": {
                "type": "integer",
                "description": "Set for reproducible results across calls.",
            },
        },
        "required": ["storage_ref", "instruction"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        storage_ref = str(args.get("storage_ref") or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        if not storage_ref or not instruction:
            return ToolResult(ok=False, data={}, error="storage_ref and instruction are required")

        loaded = load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        image_bytes, mime_type = loaded

        # An explicit `aspect_ratio` means the caller wants a DIFFERENT shape than the source
        # image, so `match_input_image` must be false for that request to actually take effect —
        # see the precedence note on `ReplicateImageProvider.edit`.
        requested_aspect_ratio = str(args["aspect_ratio"]).strip() if args.get("aspect_ratio") else None

        try:
            result = await get_image_edit_provider().edit(
                image_bytes=image_bytes,
                mime_type=mime_type,
                instruction=instruction,
                aspect_ratio=requested_aspect_ratio,
                match_input_image=requested_aspect_ratio is None,
                negative_prompt=str(args["negative_prompt"]).strip() or None if args.get("negative_prompt") else None,
                enable_prompt_expansion=bool(args.get("enable_prompt_expansion", True)),
                seed=int(args["seed"]) if args.get("seed") is not None else None,
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
