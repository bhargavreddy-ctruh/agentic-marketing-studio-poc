"""
clip_store.py — Generated video clips, kept on local disk between requests.

A Veo clip costs about ninety seconds of wall time and real money. The step
endpoints are stateless by contract, so VideoRunContext.clips started empty on
every call and the bytes were discarded when the request ended — and a
`pre_video` gate means one request per scene. Observed on a three-scene film:

    run 2   clip ready — scene_1                       99.6s
    run 3   clip ready — scene_1, clip ready — scene_2  170.4s
    run 4   clip ready — scene_1                        …

scene_1 rendered three times because nothing remembered it. For N scenes with
a gate on each, that is N(N+1)/2 renders to obtain N clips: six for three, ten
for four.

The image path solved this with `prior_assets` — the caller names what it
already holds and the step passes those through untouched. This is the same
contract, with one difference that matters: a clip is megabytes, and echoing
three of them back as base64 would blow the 32 MB request budget that exists
to stop exactly that. So the bytes stay here on disk and only an id travels.

Deliberately not a cache. Nothing here decides whether a clip is still wanted
— it cannot, since only the caller knows whether a human approved that cut.
`get` returns what was stored under an id and that is all. A scene the caller
stops naming is simply never asked for again, and the sweep below reclaims it
on age.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Optional

from .. import config_loader
from .logger import get_logger

log = get_logger(__name__)

# Under backend/ by default, so clips survive a restart and are findable when
# something looks wrong with one. A system temp directory would be tidier and
# would also disappear mid-review on some machines.
_DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "var", "campaign-clips",
)

CLIP_STORE_DIR = (
    config_loader.get("CAMPAIGN", "CLIP_STORE_DIR", "") or _DEFAULT_DIR
).strip()

# Old enough that a gate can sit open through a lunch break and still resume,
# short enough that a week of runs does not fill a disk.
CLIP_TTL_SECONDS = int(
    config_loader.get("CAMPAIGN", "CLIP_TTL_HOURS", "48") or "48"
) * 3600

CLIP_STORE_MAX_BYTES = int(
    config_loader.get("CAMPAIGN", "CLIP_STORE_MAX_BYTES", str(4 * 1024 * 1024 * 1024))
    or 4 * 1024 * 1024 * 1024
)

# A clip id reaches here from a request body. It is used to build a path, so it
# is checked against this and never merely escaped: a rejected id cannot become
# "../../config.ini" no matter what it contains.
_ID = re.compile(r"^[0-9a-f]{32}$")


def _dir() -> str:
    os.makedirs(CLIP_STORE_DIR, exist_ok=True)
    return CLIP_STORE_DIR


def _paths(clip_id: str) -> tuple[str, str]:
    base = os.path.join(CLIP_STORE_DIR, clip_id)
    return base + ".mp4", base + ".json"


def valid_id(clip_id: Any) -> bool:
    return bool(_ID.match(str(clip_id or "")))


def put(
    *,
    scene_id: str,
    data: bytes,
    mime: str = "video/mp4",
    duration_seconds: int = 0,
    motion_prompt: str = "",
) -> Optional[str]:
    """
    Write one clip and return the id it can be asked for by.

    None on failure rather than raising: a clip that generated correctly must
    not be lost because the disk it was being filed on is full. The caller
    still has the bytes in memory for this request; it just cannot offer them
    back on the next one.
    """
    if not data:
        return None
    clip_id = uuid.uuid4().hex
    video_path, meta_path = _paths(clip_id)
    try:
        _dir()
        # Written to a neighbouring name and moved into place, so a reader can
        # never open a half-written clip — os.replace is atomic within a
        # directory. The metadata lands first for the same reason: a .mp4 with
        # no sidecar reads as corrupt, a sidecar with no .mp4 reads as absent.
        with open(meta_path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({
                "clip_id": clip_id,
                "scene_id": str(scene_id or ""),
                "mime": mime or "video/mp4",
                "duration_seconds": int(duration_seconds or 0),
                "motion_prompt": str(motion_prompt or "")[:2000],
                "size_bytes": len(data),
                "written_at": time.time(),
            }, fh)
        with open(video_path + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(meta_path + ".tmp", meta_path)
        os.replace(video_path + ".tmp", video_path)
    except OSError as exc:
        log.warning("could not store clip for %s: %s", scene_id, exc)
        for stray in (video_path + ".tmp", meta_path + ".tmp", video_path, meta_path):
            try:
                os.remove(stray)
            except OSError:
                pass
        return None

    sweep()
    return clip_id


def get(clip_id: str) -> Optional[dict[str, Any]]:
    """The clip stored under this id, or None. Never raises on a bad id."""
    if not valid_id(clip_id):
        return None
    video_path, meta_path = _paths(str(clip_id))
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        with open(video_path, "rb") as fh:
            data = fh.read()
    except (OSError, ValueError):
        return None
    if not data:
        return None
    return {
        "clip_id": str(clip_id),
        "scene_id": meta.get("scene_id") or "",
        "bytes": data,
        "mime": meta.get("mime") or "video/mp4",
        "duration_seconds": int(meta.get("duration_seconds") or 0),
        "motion_prompt": meta.get("motion_prompt") or "",
    }


def _entries() -> list[tuple[float, int, str]]:
    """(written_at, size, clip_id) for everything on disk, oldest first."""
    out: list[tuple[float, int, str]] = []
    try:
        names = os.listdir(CLIP_STORE_DIR)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".mp4") or not valid_id(name[:-4]):
            continue
        clip_id = name[:-4]
        video_path, _ = _paths(clip_id)
        try:
            stat = os.stat(video_path)
        except OSError:
            continue
        out.append((stat.st_mtime, stat.st_size, clip_id))
    out.sort()
    return out


def drop(clip_id: str) -> None:
    if not valid_id(clip_id):
        return
    for path in _paths(str(clip_id)):
        try:
            os.remove(path)
        except OSError:
            pass


def sweep() -> dict[str, int]:
    """
    Reclaim by age, then by size. Called after every write.

    Age first so a run that is still going is not evicted to make room for
    itself: the oldest clip is the one least likely to belong to work in
    progress, and a film being built right now is the newest thing here.
    """
    removed = 0
    now = time.time()

    live: list[tuple[float, int, str]] = []
    for written_at, size, clip_id in _entries():
        if now - written_at > CLIP_TTL_SECONDS:
            drop(clip_id)
            removed += 1
        else:
            live.append((written_at, size, clip_id))

    total = sum(size for _, size, _ in live)
    while live and total > CLIP_STORE_MAX_BYTES:
        _written_at, size, clip_id = live.pop(0)   # oldest first, see docstring
        drop(clip_id)
        removed += 1
        total -= size

    if removed:
        log.info("clip store swept: %d removed, %d kept (%.1f MB)",
                 removed, len(live), total / 1048576)
    return {"removed": removed, "kept": len(live), "bytes": total}


def stats() -> dict[str, Any]:
    entries = _entries()
    return {
        "dir": CLIP_STORE_DIR,
        "clips": len(entries),
        "bytes": sum(size for _, size, _ in entries),
        "ttl_hours": CLIP_TTL_SECONDS // 3600,
        "max_bytes": CLIP_STORE_MAX_BYTES,
    }


__all__ = [
    "CLIP_STORE_DIR",
    "CLIP_STORE_MAX_BYTES",
    "CLIP_TTL_SECONDS",
    "drop",
    "get",
    "put",
    "stats",
    "sweep",
    "valid_id",
]
