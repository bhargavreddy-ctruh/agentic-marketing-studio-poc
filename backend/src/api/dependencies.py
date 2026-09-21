"""
FastAPI dependency wiring — the one place a concrete repository implementation is chosen and
injected behind its Protocol. Swapping SQLite for Postgres later means changing the imports in
this file only (Architecture.md section 4).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.base import get_session
from ..repositories.sqlite.sqlite_brand_repository import SqliteBrandRepository
from ..repositories.sqlite.sqlite_canvas_repository import SqliteCanvasRepository
from ..repositories.sqlite.sqlite_canvas_version_repository import SqliteCanvasVersionRepository
from ..repositories.sqlite.sqlite_mood_board_repository import SqliteMoodBoardRepository
from ..repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from ..repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
from ..services.canvas.versioning_service import CanvasVersioningService
from ..services.knowledge.brand_dna_service import BrandDnaService
from ..services.knowledge.mood_board_service import MoodBoardService
from ..services.knowledge.product_dna_service import ProductDnaService
from ..services.orchestration.session_service import SessionService

DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_session_service(db: DbSession) -> SessionService:
    return SessionService(
        sessions=SqliteSessionRepository(db), canvas=SqliteCanvasRepository(db)
    )


def get_canvas_repository(db: DbSession) -> SqliteCanvasRepository:
    return SqliteCanvasRepository(db)


def get_canvas_version_repository(db: DbSession) -> SqliteCanvasVersionRepository:
    return SqliteCanvasVersionRepository(db)


def get_session_repository(db: DbSession) -> SqliteSessionRepository:
    return SqliteSessionRepository(db)


def get_versioning_service(db: DbSession) -> CanvasVersioningService:
    return CanvasVersioningService(
        canvas=SqliteCanvasRepository(db), versions=SqliteCanvasVersionRepository(db)
    )


def get_brand_dna_service(db: DbSession) -> BrandDnaService:
    return BrandDnaService(brands=SqliteBrandRepository(db))


def get_product_dna_service(db: DbSession) -> ProductDnaService:
    return ProductDnaService(products=SqliteProductRepository(db))


def get_mood_board_service(db: DbSession) -> MoodBoardService:
    return MoodBoardService(assets=SqliteMoodBoardRepository(db))


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
CanvasRepositoryDep = Annotated[SqliteCanvasRepository, Depends(get_canvas_repository)]
CanvasVersionRepositoryDep = Annotated[SqliteCanvasVersionRepository, Depends(get_canvas_version_repository)]
SessionRepositoryDep = Annotated[SqliteSessionRepository, Depends(get_session_repository)]
VersioningServiceDep = Annotated[CanvasVersioningService, Depends(get_versioning_service)]
BrandDnaServiceDep = Annotated[BrandDnaService, Depends(get_brand_dna_service)]
ProductDnaServiceDep = Annotated[ProductDnaService, Depends(get_product_dna_service)]
MoodBoardServiceDep = Annotated[MoodBoardService, Depends(get_mood_board_service)]
