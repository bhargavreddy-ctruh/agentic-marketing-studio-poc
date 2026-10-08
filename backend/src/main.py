"""
App entry point. Boots the FastAPI app, wires middleware, mounts routers, and initializes the DB
schema. `uvicorn src.main:app --reload` from poc/backend/.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.auth.routes import router as auth_router
from .api.v1.brand.routes import router as brand_router
from .api.v1.canvas.routes import router as canvas_router
from .api.v1.guardrails.routes import router as guardrails_router
from .api.v1.mood_board.routes import router as mood_board_router
from .api.v1.product.routes import router as product_router
from .api.v1.sessions.routes import router as sessions_router
from .core.config import settings
from .core.middleware.correlation import CorrelationIdMiddleware
from .core.middleware.error_handler import register_error_handlers
from .core.middleware.logging import configure_logging, get_logger
from .models.base import async_session_factory, dispose_engine, init_models
from .providers.observability.langsmith import configure_langsmith
from .repositories.postgres.postgres_app_setting_repository import PostgresAppSettingRepository
from .repositories.postgres.postgres_brand_repository import PostgresBrandRepository
from .repositories.postgres.postgres_mood_board_repository import PostgresMoodBoardRepository
from .repositories.postgres.postgres_product_repository import PostgresProductRepository
from .services.knowledge.brand_dna_service import reindex_all_brands
from .services.knowledge.mood_board_service import reindex_all_mood_board_assets
from .services.knowledge.product_dna_service import reindex_all_products
from .services.settings.settings_service import sync_from_db as sync_settings_from_db
from .services.specialists.registry import load_all_specialists
from .services.tools.registry import load_all_tools

configure_logging(settings.log_level)
log = get_logger(__name__)


async def _settings_poll_loop() -> None:
    """Background loop (2026-09-28) — the user hand-edits `app_settings` rows directly in
    Supabase's dashboard, so there's no in-app write path to react to sooner than this. Runs for
    the life of the process; each iteration is a single cheap SELECT plus, at most, whichever
    provider resets a changed key actually needs (settings_service.sync_from_db diffs against the
    last-applied value, so an unchanged row is a no-op)."""
    while True:
        try:
            async with async_session_factory() as db:
                await sync_settings_from_db(PostgresAppSettingRepository(db))
        except Exception:
            log.exception("settings_poll_loop_iteration_failed")
        await asyncio.sleep(settings.settings_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_langsmith()
    await init_models()
    # Apply any DB-stored settings overrides BEFORE anything below gets a chance to build a
    # provider singleton off the un-overridden `.env` value.
    async with async_session_factory() as db:
        await sync_settings_from_db(PostgresAppSettingRepository(db))
    settings_poll_task = asyncio.create_task(_settings_poll_loop())
    load_all_tools()
    load_all_specialists()
    # Rehydrate LlamaIndex's in-memory Brand/Product DNA collections from the real, persisted SQL
    # records — a server restart alone must never silently make an already-onboarded brand/product
    # look "not configured" again (Memory.md, Phase 3: a real gap found via live testing).
    async with async_session_factory() as db:
        await reindex_all_brands(PostgresBrandRepository(db))
        await reindex_all_products(PostgresProductRepository(db))
        await reindex_all_mood_board_assets(PostgresMoodBoardRepository(db))

    # Startup crash-recovery: any session left in status='generating' from a previous server
    # crash or forced restart is permanently stuck — the asyncio task that was running it died
    # with the process, so it will never flip itself to 'completed' or 'error'. Without this,
    # the frontend detects status='generating' on page load, shows "Reconnecting…", and polls
    # forever. Reset every orphaned generating session to 'error' with a friendly retry prompt
    # so the user can immediately try again rather than staring at a frozen spinner.
    import json
    _recovery_prompt = json.dumps({
        "message": "The server restarted mid-generation. What would you like to do?",
        "options": [{"id": "retry", "label": "Try again", "description": "Retry the interrupted generation"}],
        "allow_free_text": True,
    })
    async with async_session_factory() as db:
        import sqlalchemy as _sa
        result = await db.execute(
            _sa.text(
                "UPDATE sessions SET status='error', next_prompt_json=:p WHERE status='generating'"
            ),
            {"p": _recovery_prompt},
        )
        await db.commit()
        recovered = result.rowcount
        if recovered:
            log.warning(
                "startup_session_recovery",
                extra={"_extra_recovered_count": recovered},
            )

    log.info("app_started", extra={"_extra_project": settings.langsmith_project})
    yield
    settings_poll_task.cancel()
    await dispose_engine()
    log.info("app_shutdown")


app = FastAPI(title="Agentic Marketing Studio POC", version="0.1.0", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)
# Phase 4a: the Next.js frontend runs on its own origin — real browser calls need real CORS,
# not just "it works from curl". Origins are config-driven (core/config.py), never hardcoded here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.frontend_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(canvas_router)
app.include_router(brand_router)
app.include_router(product_router)
app.include_router(mood_board_router)
app.include_router(guardrails_router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "langsmith_configured": bool(settings.langsmith_api_key),
        "gemini_configured": bool(settings.gemini_api_key),
        "groq_configured": bool(settings.groq_api_key),
        "falai_configured": bool(settings.falai_api_key),
        "replicate_configured": bool(settings.replicate_api_token),
    }
