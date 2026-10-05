"""
Base Video Generator tool — Architecture.md section 1b. Wraps whichever VideoGenProvider is
currently active.

Replicate (`bytedance/seedance-2.0-fast`, 2026-09-25 — replacing `prunaai/p-video`, see
`providers/video/replicate.py`'s own docstring) is the active provider and the only
currently-funded video credential; fal.ai stays registered as a provider file but is not wired
here (its key came back revoked on live testing). Swapping providers later is a one-line change in
this file, per Rules.md section 1/2 — no specialist or Lead file changes.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.exceptions import ProviderUnavailable
from ...core.local_storage import load_asset, save_asset
from ...providers.video.replicate import get_video_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("base_video_generator")
class BaseVideoGeneratorTool(Tool):
    name = "base_video_generator"
    description = (
        "Generates a short video clip by animating a source image with a motion prompt. "
        "model/resolution/duration_seconds/camera_motion are REQUIRED, no silent default — decide "
        "each explicitly from what was actually asked (e.g. bytedance/seedance-2.0-fast for a "
        "quick/cheap turnaround, google/veo for richer native audio or higher native resolution)."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "source_image_storage_ref": {"type": "string"},
            "last_frame_storage_ref": {
                "type": "string",
                "description": (
                    "Optional: the PREVIOUS clip's last frame, for multi-shot continuity — chains "
                    "this clip's start to where the previous one ended instead of each shot looking "
                    "like an unrelated still."
                ),
            },
            "camera_motion": {
                "type": "string",
                "description": (
                    "The camera's own motion, described separately from subject motion (e.g. "
                    "'slow dolly in', 'static locked-off shot', 'tracking shot following left') — "
                    "REQUIRED, no default. Conflating camera and subject motion in one phrase is a "
                    "known cause of warping/jitter on this model family."
                ),
            },
            "aspect_ratio": {"type": "string"},
            "resolution": {
                "type": "string",
                "description": "REQUIRED, no default — decide explicitly (e.g. '720p') every call.",
            },
            "duration_seconds": {
                "type": "integer",
                "description": (
                    "REQUIRED, no default — derive from what the user actually asked for (an "
                    "explicit length, 'keep it quick', a multi-beat ask), not a flat assumption."
                ),
            },
            "model": {
                "type": "string",
                "enum": ["bytedance/seedance-2.0-fast", "google/veo"],
                "description": "REQUIRED, no default — pick explicitly every call.",
            },
            "generate_audio": {
                "type": "boolean",
                "description": (
                    "Whether the video model should generate its OWN native synchronized audio "
                    "(ambient/dialogue/background). Set this explicitly: false when a separate "
                    "scripted voiceover (sound_designer) will be muxed in instead, true/omit when "
                    "the model's native audio is itself what's wanted."
                ),
            },
        },
        "required": ["prompt", "source_image_storage_ref", "model", "resolution", "duration_seconds", "camera_motion"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        prompt = str(args.get("prompt") or "").strip()
        source_ref = str(args.get("source_image_storage_ref") or "").strip()
        if not prompt or not source_ref:
            return ToolResult(
                ok=False, data={}, error="prompt and source_image_storage_ref are required"
            )
        model = str(args.get("model") or "").strip()
        resolution = str(args.get("resolution") or "").strip()
        duration_seconds = args.get("duration_seconds")
        if not model or not resolution or duration_seconds is None:
            return ToolResult(
                ok=False, data={},
                error="model, resolution, and duration_seconds are required — no silent default",
            )

        loaded = await load_asset(source_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {source_ref}")
        image_bytes, _mime_type = loaded

        last_frame_bytes = None
        last_frame_ref = str(args.get("last_frame_storage_ref") or "").strip()
        if last_frame_ref:
            loaded_last_frame = await load_asset(last_frame_ref)
            if loaded_last_frame is not None:
                last_frame_bytes = loaded_last_frame[0]

        provider = get_video_provider()
        try:
            result = await provider.generate(
                prompt=prompt,
                image_bytes=image_bytes,
                last_frame_bytes=last_frame_bytes,
                duration_seconds=int(duration_seconds),
                aspect_ratio=str(args.get("aspect_ratio") or "16:9"),
                resolution=resolution,
                model=model,
                camera_motion=str(args.get("camera_motion") or "").strip() or None,
                generate_audio=args.get("generate_audio"),
            )
        except ProviderUnavailable as exc:
            return ToolResult(ok=False, data={}, error=exc.message)

        storage_ref = await save_asset(
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
