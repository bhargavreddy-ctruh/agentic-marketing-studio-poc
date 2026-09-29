"""
Real, live-found gap (2026-09-30): `core/local_storage.py`'s metadata sidecar (a local JSON file,
`var/assets/{storage_ref}.json`) is where a storage_ref's Cloudinary url/mime_type has always
lived — fine on one machine, but a session created on one deploy (e.g. a laptop) and viewed from
another (e.g. a fresh EC2 instance) can never resolve its own assets: the bytes are safely on
Cloudinary, but the mapping to find them only exists as a file on the machine that created it.
This mirrors that same mapping into Supabase — the one store every deploy already shares — so any
machine can resolve any storage_ref. The local sidecar stays the fast, synchronous primary path
(unchanged, zero risk to its ~27 existing call sites); this table is a same-shape fallback,
consulted only by `local_storage.py`'s new `_async` functions.
"""
from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class AssetMetadataModel(Base, TimestampMixin):
    __tablename__ = "asset_metadata"

    storage_ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    cloudinary_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cloudinary_resource_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Everything else the local sidecar carries (source, prompt, provider, product_id, etc.) —
    # kept for parity/debuggability, never required to resolve an asset's bytes.
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict)
