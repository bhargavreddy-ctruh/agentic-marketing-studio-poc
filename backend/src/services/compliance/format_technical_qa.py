"""
Format/Technical QA — Architecture.md section 1a (trimmed Compliance set). The one Compliance
checker in this POC that's fully mechanical, not an LLM guess: checks a canvas element's ACTUAL
media dimensions (via Pillow for images, `ffprobe` for video) against the aspect ratio actually
requested at generation time — "does the file match its own recorded metadata" needs no reasoning.
"""
from __future__ import annotations

import asyncio
import io
import json
import tempfile
from pathlib import Path

from PIL import Image

from ...core.local_storage import load_asset
from ...core.middleware.logging import get_logger
from ...providers.observability.langsmith import traceable

log = get_logger(__name__)

_KNOWN_RATIOS = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:5": 4 / 5, "3:2": 3 / 2}


def _closest_aspect_ratio(width: int, height: int) -> str:
    actual = width / height
    return min(_KNOWN_RATIOS, key=lambda k: abs(_KNOWN_RATIOS[k] - actual))


async def _probe_video_dimensions(data: bytes) -> tuple[int | None, int | None]:
    with tempfile.TemporaryDirectory() as tmp:
        clip_path = Path(tmp) / "clip.mp4"
        clip_path.write_bytes(data)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(clip_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return None, None
        stdout, _stderr = await proc.communicate()
        if proc.returncode != 0:
            return None, None
        try:
            stream = json.loads(stdout)["streams"][0]
            return stream["width"], stream["height"]
        except (KeyError, IndexError, json.JSONDecodeError):
            return None, None


@traceable(name="format_technical_qa")
async def check_format_technical(*, storage_ref: str, expected_aspect_ratio: str | None) -> dict:
    loaded = load_asset(storage_ref)
    if loaded is None:
        return {"passed": False, "reason": f"no asset found for storage_ref {storage_ref}"}
    data, mime_type = loaded

    if mime_type.startswith("image/"):
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    elif mime_type == "video/mp4":
        width, height = await _probe_video_dimensions(data)
        if width is None:
            return {"passed": False, "reason": "could not read video dimensions via ffprobe"}
    else:
        return {"passed": False, "reason": f"unsupported mime type '{mime_type}' for format check"}

    actual_ratio = _closest_aspect_ratio(width, height)
    passed = expected_aspect_ratio is None or actual_ratio == expected_aspect_ratio
    return {
        "passed": passed,
        "width": width,
        "height": height,
        "actual_aspect_ratio": actual_ratio,
        "expected_aspect_ratio": expected_aspect_ratio,
        "reason": "" if passed else f"expected {expected_aspect_ratio}, got {actual_ratio} ({width}x{height})",
    }
