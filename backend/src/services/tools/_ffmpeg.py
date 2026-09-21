"""
Shared local-`ffmpeg` subprocess runner.

Extracted per Rules.md's DRY rule: `video_stitcher.py` and `audio_video_muxer.py` both run a real
ffmpeg command against temp files and need identical error handling — a real duplication found on
review, not left copied twice a second time.
"""
from __future__ import annotations

import asyncio


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
