"""
Audio/Video Muxer tool — combines a real, separately-produced audio track (e.g. Sound Designer's
`text_to_speech` output) onto an existing video's own container, via the local `ffmpeg` binary,
same subprocess pattern as `video_stitcher.py`.

Deliberately its own tool, not something a Lead executor calls automatically: whether a given
voiceover actually belongs muxed into the final file, versus kept as a separate asset for later
review, is a real creative/product decision — Rules.md's own instruction against hard-wiring a
capability applies here exactly as it did for `text_overlay`/`video_stitcher`. A specialist
(`sound_designer`) decides for itself, given both storage_refs in its context, whether to call
this at all.

`-shortest` caps the muxed output at whichever of the two inputs is shorter — a genuine constraint
of combining two independently-produced clips, disclosed here rather than silently truncating one
of them with no explanation.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import ClassVar

from ...core.local_storage import load_asset, save_asset
from ._ffmpeg import run_ffmpeg
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("mux_audio_into_video")
class AudioVideoMuxerTool(Tool):
    name = "mux_audio_into_video"
    description = (
        "Combines an existing audio track onto an existing video's own container, producing one "
        "new video file with real audio. Output length is capped at the shorter of the two inputs."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "video_storage_ref": {"type": "string"},
            "audio_storage_ref": {"type": "string"},
        },
        "required": ["video_storage_ref", "audio_storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        video_ref = str(args.get("video_storage_ref") or "").strip()
        audio_ref = str(args.get("audio_storage_ref") or "").strip()
        if not video_ref or not audio_ref:
            return ToolResult(ok=False, data={}, error="video_storage_ref and audio_storage_ref are required")

        video_loaded = await load_asset(video_ref)
        if video_loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {video_ref}")
        audio_loaded = await load_asset(audio_ref)
        if audio_loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {audio_ref}")

        video_bytes, _video_mime = video_loaded
        audio_bytes, _audio_mime = audio_loaded

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio_path = tmp_path / "in_audio.wav"
            output_path = tmp_path / "muxed.mp4"
            
            is_image = _video_mime.startswith("image/")
            if is_image:
                ext = _video_mime.split("/")[-1]
                video_path = tmp_path / f"in_image.{ext}"
            else:
                video_path = tmp_path / "in_video.mp4"
                
            video_path.write_bytes(video_bytes)
            audio_path.write_bytes(audio_bytes)

            if is_image:
                error = await run_ffmpeg(
                    "-loop", "1", "-framerate", "25",
                    "-i", str(video_path), "-i", str(audio_path),
                    "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac",
                    "-b:a", "192k", "-pix_fmt", "yuv420p", "-shortest",
                    str(output_path),
                )
            else:
                error = await run_ffmpeg(
                    "-i", str(video_path), "-i", str(audio_path),
                    "-c:v", "copy", "-c:a", "aac",
                    "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                    str(output_path),
                )
            if error or not output_path.exists():
                return ToolResult(ok=False, data={}, error=error or "ffmpeg produced no output")

            muxed_bytes = output_path.read_bytes()

        storage_ref = await save_asset(
            muxed_bytes, "video/mp4", metadata={"muxed_video_from": video_ref, "muxed_audio_from": audio_ref}
        )
        return ToolResult(ok=True, data={"storage_ref": storage_ref, "mime_type": "video/mp4"})
