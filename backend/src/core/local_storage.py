"""
Local-disk asset storage — Architecture.md's "generated file storage" choice, same pattern as the
existing agentic_flow codebase's clip_store.py (files + a small metadata sidecar, referenced by
id). This is the ONE place that touches the filesystem for generated assets; swapping to an object
store later (Architecture.md section 4) means changing this file only.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "var" / "assets"

_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/webm": "weba",
    "text/plain": "txt",
}


def save_asset(data: bytes, mime_type: str, *, metadata: dict | None = None) -> str:
    """Writes bytes + a metadata sidecar to disk, returns an opaque storage_ref (an id)."""
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    asset_id = uuid.uuid4().hex
    ext = _EXT_BY_MIME.get(mime_type, "bin")
    data_path = _STORAGE_DIR / f"{asset_id}.{ext}"
    meta_path = _STORAGE_DIR / f"{asset_id}.json"

    data_path.write_bytes(data)
    meta_path.write_text(
        json.dumps({"mime_type": mime_type, **(metadata or {})}, default=str), encoding="utf-8"
    )
    return asset_id


def load_asset(storage_ref: str) -> tuple[bytes, str] | None:
    """Returns (bytes, mime_type) for a storage_ref, or None if it doesn't exist."""
    meta_candidates = list(_STORAGE_DIR.glob(f"{storage_ref}.json"))
    if not meta_candidates:
        return None
    meta = json.loads(meta_candidates[0].read_text(encoding="utf-8"))
    mime_type = meta.get("mime_type", "application/octet-stream")
    ext = _EXT_BY_MIME.get(mime_type, "bin")
    data_path = _STORAGE_DIR / f"{storage_ref}.{ext}"
    if not data_path.exists():
        return None
    return data_path.read_bytes(), mime_type
