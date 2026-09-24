"""
The declarative base + the async engine/session factory.

Rules.md section 2: models/ never imports from services/ or schemas/ — persistence schema only.
SQLAlchemy is used specifically (over raw sqlite3) so switching database_url to a Postgres/Supabase
DSN later is a config change, not a rewrite — see Architecture.md section 4 (portability).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..core.config import settings


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


_engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_models() -> None:
    """Create tables if they don't exist. Fine for a POC; a real migration tool comes later."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Lightweight column migrations for SQLite
        migrations = [
            ("product_profiles", "user_id", "VARCHAR(36)"),
            ("product_profiles", "photo_storage_ref", "VARCHAR(255)"),
            ("brand_profiles", "user_id", "VARCHAR(36)"),
            ("brand_profiles", "logo_storage_ref", "VARCHAR(255)"),
            ("brand_profiles", "font_storage_refs", "JSON"),
            ("sessions", "user_id", "VARCHAR(36)"),
            ("sessions", "style_ref_storage_ref", "VARCHAR(255)"),
            ("sessions", "style_seed", "INTEGER"),
            ("sessions", "approval_mode", "VARCHAR(16)"),
            ("sessions", "next_prompt_json", "JSON"),
            ("canvas_elements", "pending_storage_ref", "VARCHAR(255)"),
            ("canvas_elements", "pending_metadata", "JSON"),
            ("canvas_elements", "pending_action", "VARCHAR(32)"),
            ("canvas_elements", "compliance_status", "VARCHAR(16)"),
            ("canvas_elements", "ad_spec_name", "VARCHAR(64)"),
            ("canvas_elements", "safe_zone_pct", "FLOAT"),
            ("canvas_element_versions", "element_type", "VARCHAR(32)"),
        ]
        from sqlalchemy import text
        for table, col, col_type in migrations:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
            except Exception:
                pass


def get_engine():
    return _engine


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
