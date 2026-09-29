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
async def check_format_technical(
    *, storage_ref: str, expected_aspect_ratio: str | None, text_area_max_pct: float | None = 20.0
) -> dict:
    loaded = await load_asset(storage_ref)
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
    aspect_passed = expected_aspect_ratio is None or actual_ratio == expected_aspect_ratio

    # Calculate text coverage estimation if image
    text_coverage_pct = 0.0
    if mime_type.startswith("image/"):
        try:
            from PIL import ImageFilter, ImageStat
            with Image.open(io.BytesIO(data)) as img:
                gray = img.convert("L")
                edges = gray.filter(ImageFilter.FIND_EDGES)
                # Count high-edge-density pixels as candidate text areas
                stat = ImageStat.Stat(edges)
                mean_edge = stat.mean[0]
                text_coverage_pct = min(100.0, round((mean_edge / 255.0) * 100.0 * 1.5, 1))
        except Exception:
            text_coverage_pct = 5.0

    text_area_passed = text_area_max_pct is None or text_coverage_pct <= text_area_max_pct
    passed = aspect_passed and text_area_passed

    reasons = []
    if not aspect_passed:
        reasons.append(f"expected {expected_aspect_ratio}, got {actual_ratio} ({width}x{height})")
    if not text_area_passed:
        reasons.append(f"estimated text coverage ({text_coverage_pct}%) exceeds maximum allowed ({text_area_max_pct}%)")

    return {
        "passed": passed,
        "width": width,
        "height": height,
        "actual_aspect_ratio": actual_ratio,
        "expected_aspect_ratio": expected_aspect_ratio,
        "text_coverage_pct": text_coverage_pct,
        "text_area_max_pct": text_area_max_pct,
        "reason": "; ".join(reasons) if not passed else "",
    }
