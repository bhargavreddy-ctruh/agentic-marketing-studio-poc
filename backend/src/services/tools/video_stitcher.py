"""
Video Stitcher tool — Architecture.md section 1b. Concatenates one or more existing video clips
(by storage_ref, in order) into a single final video using the local `ffmpeg` binary via
subprocess — a direct port of the existing agentic_flow codebase's FFmpeg-based approach
(Rules.md section 4), not a new vendor dependency.

Runs even for a single input clip (Motion Lead's current cost-capped default of one shot per job —
Memory.md, Phase 2): that still exercises the real ffmpeg code path end-to-end, just with nothing
to actually join, at zero added cost since ffmpeg runs locally, not against a paid API.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import ClassVar

from ...core.local_storage import load_asset, save_asset
from ._ffmpeg import run_ffmpeg
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("video_stitcher")
class VideoStitcherTool(Tool):
    name = "video_stitcher"
    description = "Concatenates one or more video clips (by storage_ref) into a single video, in order."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "storage_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["storage_refs"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        storage_refs = args.get("storage_refs") or []
        if not storage_refs or not isinstance(storage_refs, list):
            return ToolResult(ok=False, data={}, error="storage_refs must be a non-empty list")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            concat_lines = []
            for i, ref in enumerate(storage_refs):
                ref = str(ref).strip()
                loaded = load_asset(ref)
                if loaded is None:
                    return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {ref}")
                video_bytes, _mime = loaded
                clip_path = tmp_path / f"clip_{i}.mp4"
                clip_path.write_bytes(video_bytes)
                concat_lines.append(f"file '{clip_path}'")

            list_path = tmp_path / "concat_list.txt"
            list_path.write_text("\n".join(concat_lines), encoding="utf-8")
            output_path = tmp_path / "stitched.mp4"

            error = await run_ffmpeg(
                "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)
            )
            if error or not output_path.exists():
                return ToolResult(ok=False, data={}, error=error or "ffmpeg produced no output")

            stitched_bytes = output_path.read_bytes()

        storage_ref = save_asset(
            stitched_bytes, "video/mp4", metadata={"stitched_from": storage_refs}
        )
        return ToolResult(
            ok=True,
            data={"storage_ref": storage_ref, "mime_type": "video/mp4", "clip_count": len(storage_refs)},
        )
