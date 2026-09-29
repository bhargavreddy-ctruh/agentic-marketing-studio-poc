"""
Asset storage — Cloudinary for bytes, Supabase Postgres for metadata. This is the ONE place that
touches asset storage anywhere in the app; every other module only ever calls
`save_asset`/`load_asset`/`public_url`/`asset_mime_type` (never a filesystem path or a vendor SDK
directly), so the storage backend itself can be swapped here without touching any of those call
sites.

Real, live-found incident (2026-09-30): this used to keep the storage_ref -> Cloudinary-url
mapping as a LOCAL JSON sidecar file (`var/assets/{storage_ref}.json`) — fine on one machine, but
a session created on one deploy and viewed from another (e.g. a laptop, then a fresh EC2 instance)
could never resolve its own assets: the bytes were safely on Cloudinary, but the mapping to find
them only existed as a file on the machine that created it. Per explicit instruction ("there is no
local storage anymore so supabase is one and only"), local disk is no longer a storage backend at
all — Cloudinary (bytes) + Supabase's `asset_metadata` table (the mapping) are the only store,
resolvable from any machine. `CLOUDINARY_URL` is therefore REQUIRED now, same "no silent fallback"
principle already applied to `DATABASE_URL`/`OLLAMA_BASE_URL` in docker-compose.yml.

Every function here is now async (a real Postgres round-trip, not a local file read) — every
caller across the app is already inside an `async def` (every `Tool.run()`, every service method),
so this is `await` added at each call site, not an event-loop change.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from io import BytesIO

import httpx
from sqlalchemy import select

from ..models.asset_metadata import AssetMetadataModel
from ..models.base import async_session_factory
from .exceptions import ProviderUnavailable
from .middleware.logging import get_logger

log = get_logger(__name__)


def _resource_type_for(mime_type: str) -> str:
    # Cloudinary's own three upload buckets — anything that isn't image/video (audio, plain text,
    # unknown binary) has to go through "raw", which skips its image/video-only transformations.
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    return "raw"


def _upload_to_cloudinary(data: bytes, asset_id: str, resource_type: str) -> dict:
    """Cloudinary's SDK is synchronous (blocking network I/O) — always called via
    `asyncio.to_thread` below, never directly, so one upload never blocks the event loop (and
    every other concurrent request) while it's in flight."""
    import cloudinary.uploader

    return cloudinary.uploader.upload(
        BytesIO(data), public_id=asset_id, resource_type=resource_type, overwrite=True
    )


async def save_asset(data: bytes, mime_type: str, *, metadata: dict | None = None) -> str:
    """Uploads bytes to Cloudinary and records the mapping in Supabase, returns an opaque
    storage_ref (an id) — unchanged contract from the pre-async version, just awaited now.
    `CLOUDINARY_URL` (DB-managed via Supabase's app_settings, applied to os.environ by
    settings_service.py) is required — no local-disk fallback."""
    if not os.environ.get("CLOUDINARY_URL"):
        raise ProviderUnavailable(
            "local_storage",
            "CLOUDINARY_URL is not configured — set it in Supabase's app_settings table "
            "(key: cloudinary_url). Local disk is no longer a supported asset store.",
        )

    asset_id = uuid.uuid4().hex
    resource_type = _resource_type_for(mime_type)
    result = await asyncio.to_thread(_upload_to_cloudinary, data, asset_id, resource_type)
    cloudinary_url = result["secure_url"]

    async with async_session_factory() as db:
        db.add(
            AssetMetadataModel(
                storage_ref=asset_id,
                mime_type=mime_type,
                cloudinary_url=cloudinary_url,
                cloudinary_resource_type=resource_type,
                extra_json=metadata or {},
            )
        )
        await db.commit()

    return asset_id


async def _get_metadata(storage_ref: str) -> AssetMetadataModel | None:
    async with async_session_factory() as db:
        result = await db.execute(
            select(AssetMetadataModel).where(AssetMetadataModel.storage_ref == storage_ref)
        )
        return result.scalar_one_or_none()


async def get_metadata_batch(storage_refs: list[str]) -> dict[str, AssetMetadataModel]:
    """Real, live-found incident (2026-09-30): rendering a canvas with N elements used to call
    `public_url()`/`_text_content()` for each one via `asyncio.gather` — N simultaneous DB
    sessions, each checking out its own connection from Supabase's Session Pooler at once. A
    canvas with more elements than the pooler's `pool_size` (15) genuinely exhausted it
    (`InternalError: max clients reached in session mode`), a real 500 on a live deploy. One
    query, one connection, for however many refs a caller actually needs — used by
    `mappers/canvas_mapper.py` to resolve an entire canvas state's worth of urls/mime-types in a
    single round trip instead of one per element."""
    refs = [r for r in set(storage_refs) if r]
    if not refs:
        return {}
    async with async_session_factory() as db:
        result = await db.execute(
            select(AssetMetadataModel).where(AssetMetadataModel.storage_ref.in_(refs))
        )
        return {row.storage_ref: row for row in result.scalars().all()}


async def load_asset(storage_ref: str) -> tuple[bytes, str] | None:
    """Returns (bytes, mime_type) for a storage_ref, or None if it doesn't exist."""
    meta = await _get_metadata(storage_ref)
    if meta is None or not meta.cloudinary_url:
        return None
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(meta.cloudinary_url)
    if response.status_code != 200:
        return None
    return response.content, meta.mime_type


async def asset_mime_type(storage_ref: str) -> str | None:
    """The mime_type recorded for a storage_ref — a plain metadata lookup, no bytes downloaded,
    unlike `load_asset()`. Lets a caller that only needs to know WHETHER an asset is an image
    (not its bytes) skip downloading a Cloudinary-hosted asset entirely."""
    meta = await _get_metadata(storage_ref)
    return meta.mime_type if meta is not None else None


async def public_url(storage_ref: str | None) -> str | None:
    """The real Cloudinary URL behind a storage_ref, when one exists — lets callers (canvas
    responses, the vision LLM path) hand a browser/model provider a direct CDN link instead of
    round-tripping bytes through this backend a second time. Returns None for a bare/unknown
    storage_ref."""
    if not storage_ref:
        return None
    meta = await _get_metadata(storage_ref)
    return meta.cloudinary_url if meta is not None else None
