"""
Shared local-`ffmpeg` subprocess runner.

Extracted per Rules.md's DRY rule: `video_stitcher.py` and `audio_video_muxer.py` both run a real
ffmpeg command against temp files and need identical error handling — a real duplication found on
review, not left copied twice a second time.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


async def run_ffmpeg(*args: str) -> str | None:
    """Runs `ffmpeg -y <args>`. Returns None on success, or a real error string on failure — never
    raises, so a Tool can turn the result straight into a `ToolResult` with no try/except of its
    own."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return "ffmpeg binary not found on this host"

    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return f"ffmpeg failed: {stderr.decode()[:300]}"
    return None


async def extract_last_frame(video_bytes: bytes) -> bytes | None:
    """Scene-to-scene continuity (Memory.md fidelity audit, 2026-10-05): a multi-shot video needs
    each clip's starting frame to visually continue from the previous clip's END, not an unrelated
    still — `ReplicateVideoProvider.generate()` already accepts a `last_frame_bytes` keyframe, this
    is the one piece that was missing: actually extracting that last frame from the previous clip's
    own video bytes. Returns PNG bytes, or None on failure (caller degrades to no continuity frame
    rather than failing the whole shot — a missing continuity anchor is not worth losing an
    otherwise-good clip over)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "last_frame.png"
        input_path.write_bytes(video_bytes)
        error = await run_ffmpeg(
            "-sseof", "-1", "-i", str(input_path), "-update", "1", "-q:v", "2", str(output_path)
        )
        if error or not output_path.exists():
            return None
        return output_path.read_bytes()
