"""
The declarative base + the async engine/session factory.

Rules.md section 2: models/ never imports from services/ or schemas/ — persistence schema only.
`database_url` (core/config.py) is Postgres/Supabase only — this app's one real connection
(2026-09-30, no fallback, per an explicit user ask).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..core.config import settings


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


_engine = create_async_engine(
    settings.database_url,
    echo=False,
    # Real, live-found incident (2026-09-30): Supabase's Session Pooler caps a project at 15 total
    # connections project-wide — SQLAlchemy's own defaults (pool_size=5 + max_overflow=10 = 15)
    # meant this ONE backend process could alone consume the project's ENTIRE budget, and a leak
    # (a redeploy whose old container's connections weren't cleanly released) pegged it there for
    # 15+ hours, breaking every real request (`canvas`/`sessions` routes) with a raw
    # `asyncpg.exceptions.InternalServerError: max clients reached`.
    pool_size=3,
    max_overflow=2,
    # Detects a stale/dead connection (the far end silently closed it) before handing it to a
    # request, transparently reconnecting instead of the app holding a broken connection open
    # indefinitely — a real contributor to how connections accumulated unused for hours.
    pool_pre_ping=True,
    # Forces every pooled connection to be recycled after 5 minutes, regardless of use — a hard
    # ceiling on how long any single connection can sit in the pool, so a future leak of this same
    # shape can no longer hold a slot for 15+ hours the way this incident's did.
    pool_recycle=300,
)
async_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def dispose_engine() -> None:
    """Real, live-found gap alongside the incident above: a graceful shutdown never closed this
    engine's pool at all — `main.py`'s `lifespan()` only ever cancelled the settings poll task,
    relying entirely on the OS closing sockets when the process exits. That's normally enough, but
    is not a substitute for an explicit, deterministic close — call this from `lifespan()`'s
    shutdown path so a graceful restart always releases every pooled connection immediately."""
    await _engine.dispose()


async def init_models() -> None:
    """Create tables if they don't exist. Fine for a POC; a real migration tool comes later."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight column migrations — Postgres (Supabase) only; this app's only real
        # connection (2026-09-30, per an explicit user ask to drop dead SQLite-only code paths
        # now that no deployment ever runs against SQLite).
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
            # Explicit DEFAULT TRUE (not just the model's own Python-side default), so an existing
            # session's already-stored NULL from before this migration reads back as guardrails ON,
            # never as falsy/off — a real gap the other nullable-with-a-default columns above don't
            # have to worry about since Python None there just means "not set yet." TRUE (not the
            # SQLite-only integer literal `1`) so this same statement works verbatim on Postgres too.
            ("sessions", "guardrails_enabled", "BOOLEAN DEFAULT TRUE"),
            ("canvas_elements", "pending_storage_ref", "VARCHAR(255)"),
            ("canvas_elements", "pending_metadata", "JSON"),
            ("canvas_elements", "pending_action", "VARCHAR(32)"),
            ("canvas_elements", "compliance_status", "VARCHAR(16)"),
            ("canvas_elements", "ad_spec_name", "VARCHAR(64)"),
            ("canvas_elements", "safe_zone_pct", "FLOAT"),
            # campaign_id/campaign_name (2026-09-25) discarded same-day per explicit user
            # correction — a workflow IS one campaign; grouping is by real product instead (see
            # `models/canvas_element.py`). Those two columns are left as harmless dead columns;
            # nothing reads them any more.
            ("canvas_elements", "product_id", "VARCHAR(36)"),
            ("canvas_elements", "product_name", "VARCHAR(255)"),
            ("canvas_elements", "parent_element_id", "VARCHAR(36)"),
            ("canvas_element_versions", "element_type", "VARCHAR(32)"),
        ]
        from sqlalchemy import text

        # Postgres's `ADD COLUMN IF NOT EXISTS` — cleaner and race-free than a try/except-swallow.
        for table, col, col_type in migrations:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}")
            )


def get_engine():
    return _engine


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
