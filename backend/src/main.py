"""
App entry point. Boots the FastAPI app, wires middleware, mounts routers, and initializes the DB
schema. `uvicorn src.main:app --reload` from poc/backend/.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.auth.routes import router as auth_router
from .api.v1.brand.routes import router as brand_router
from .api.v1.canvas.routes import router as canvas_router
from .api.v1.mood_board.routes import router as mood_board_router
from .api.v1.product.routes import router as product_router
from .api.v1.sessions.routes import router as sessions_router
from .api.v1.guardrails.routes import router as guardrails_router
from .core.config import settings
from .core.middleware.correlation import CorrelationIdMiddleware
from .core.middleware.error_handler import register_error_handlers
from .core.middleware.logging import configure_logging, get_logger
from .models.base import async_session_factory, init_models
from .providers.observability.langsmith import configure_langsmith
from .repositories.sqlite.sqlite_brand_repository import SqliteBrandRepository
from .repositories.sqlite.sqlite_mood_board_repository import SqliteMoodBoardRepository
from .repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from .services.knowledge.brand_dna_service import reindex_all_brands
from .services.knowledge.mood_board_service import reindex_all_mood_board_assets
from .services.knowledge.product_dna_service import reindex_all_products
from .services.specialists.registry import load_all_specialists
from .services.tools.registry import load_all_tools

configure_logging(settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_langsmith()
    await init_models()
    load_all_tools()
    load_all_specialists()
    # Rehydrate LlamaIndex's in-memory Brand/Product DNA collections from the real, persisted SQL
    # records — a server restart alone must never silently make an already-onboarded brand/product
    # look "not configured" again (Memory.md, Phase 3: a real gap found via live testing).
    async with async_session_factory() as db:
        await reindex_all_brands(SqliteBrandRepository(db))
        await reindex_all_products(SqliteProductRepository(db))
        await reindex_all_mood_board_assets(SqliteMoodBoardRepository(db))
    log.info("app_started", extra={"_extra_project": settings.langsmith_project})
    yield
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
        "openrouter_configured": bool(settings.openrouter_api_key),
        "huggingface_configured": bool(settings.huggingface_api_token),
        "falai_configured": bool(settings.falai_api_key),
    }
