"""
Asset storage — Architecture.md's "generated file storage" choice, same pattern as the existing
agentic_flow codebase's clip_store.py (files + a small metadata sidecar, referenced by id). This is
the ONE place that touches asset storage anywhere in the app; every other module only ever calls
`save_asset`/`load_asset` (never a filesystem path or a vendor SDK directly), so the storage backend
itself can be swapped here without touching any of those call sites.

Free-tier deploy (2026-09-28): the actual media bytes move to Cloudinary when `CLOUDINARY_URL` is
set, but `storage_ref` stays the exact same opaque asset-id string it always was — a Cloudinary
secure_url can't be used as-is (it contains "/", which breaks the `/assets/{storage_ref}` route's
path matching, and the DB columns that store it are `VARCHAR(255)`, not built for a full URL). The
metadata sidecar (already written locally for every asset, always has been) now also carries the
Cloudinary secure_url instead of the raw bytes ever touching disk on this backend. Falls back to
writing bytes straight to disk when `CLOUDINARY_URL` is unset, so local dev needs no Cloudinary
account at all.
"""
from __future__ import annotations

import json
import os
import uuid
from io import BytesIO
from pathlib import Path

import httpx

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


def _cloudinary_enabled() -> bool:
    return bool(os.environ.get("CLOUDINARY_URL"))


def _resource_type_for(mime_type: str) -> str:
    # Cloudinary's own three upload buckets — anything that isn't image/video (audio, plain text,
    # unknown binary) has to go through "raw", which skips its image/video-only transformations.
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    return "raw"


def save_asset(data: bytes, mime_type: str, *, metadata: dict | None = None) -> str:
    """Writes bytes + a metadata sidecar, returns an opaque storage_ref (an id) — unchanged
    contract regardless of which backend actually holds the bytes."""
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    asset_id = uuid.uuid4().hex
    meta_path = _STORAGE_DIR / f"{asset_id}.json"
    meta: dict = {"mime_type": mime_type, **(metadata or {})}

    if _cloudinary_enabled():
        import cloudinary.uploader

        resource_type = _resource_type_for(mime_type)
        result = cloudinary.uploader.upload(
            BytesIO(data),
            public_id=asset_id,
            resource_type=resource_type,
            overwrite=True,
        )
        meta["cloudinary_url"] = result["secure_url"]
        meta["cloudinary_resource_type"] = resource_type
    else:
        ext = _EXT_BY_MIME.get(mime_type, "bin")
        (_STORAGE_DIR / f"{asset_id}.{ext}").write_bytes(data)

    meta_path.write_text(json.dumps(meta, default=str), encoding="utf-8")
    return asset_id


def load_asset(storage_ref: str) -> tuple[bytes, str] | None:
    """Returns (bytes, mime_type) for a storage_ref, or None if it doesn't exist."""
    meta_candidates = list(_STORAGE_DIR.glob(f"{storage_ref}.json"))
    if not meta_candidates:
        return None
    meta = json.loads(meta_candidates[0].read_text(encoding="utf-8"))
    mime_type = meta.get("mime_type", "application/octet-stream")

    cloudinary_url = meta.get("cloudinary_url")
    if cloudinary_url:
        response = httpx.get(cloudinary_url, timeout=30.0)
        if response.status_code != 200:
            return None
        return response.content, mime_type

    ext = _EXT_BY_MIME.get(mime_type, "bin")
    data_path = _STORAGE_DIR / f"{storage_ref}.{ext}"
    if not data_path.exists():
        return None
    return data_path.read_bytes(), mime_type


def asset_mime_type(storage_ref: str) -> str | None:
    """The mime_type recorded in a storage_ref's metadata sidecar — a plain local file read, no
    network call, unlike `load_asset()`. Lets a caller that only needs to know WHETHER an asset is
    an image (not its bytes) skip downloading a Cloudinary-hosted asset entirely."""
    meta_candidates = list(_STORAGE_DIR.glob(f"{storage_ref}.json"))
    if not meta_candidates:
        return None
    meta = json.loads(meta_candidates[0].read_text(encoding="utf-8"))
    return meta.get("mime_type")


def public_url(storage_ref: str | None) -> str | None:
    """The real Cloudinary URL behind a storage_ref, when one exists — lets callers (canvas
    responses, the vision LLM path) hand a browser/model provider a direct CDN link instead of
    round-tripping bytes through this backend a second time. Returns None for a bare/unknown
    storage_ref OR when running in local-disk mode (no CLOUDINARY_URL configured) — callers fall
    back to the existing `/api/v1/canvas/assets/{storage_ref}` proxy route in either case, so
    nothing regresses in dev."""
    if not storage_ref:
        return None
    meta_candidates = list(_STORAGE_DIR.glob(f"{storage_ref}.json"))
    if not meta_candidates:
        return None
    meta = json.loads(meta_candidates[0].read_text(encoding="utf-8"))
    return meta.get("cloudinary_url")
