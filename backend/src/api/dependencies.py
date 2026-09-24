"""
FastAPI dependency wiring — the one place a concrete repository implementation is chosen and
injected behind its Protocol. Swapping SQLite for Postgres later means changing the imports in
this file only (Architecture.md section 4).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import Unauthorized
from ..core.security import verify_session_token
from ..models.base import get_session
from ..models.user import UserModel
from ..repositories.sqlite.sqlite_brand_repository import SqliteBrandRepository
from ..repositories.sqlite.sqlite_canvas_repository import SqliteCanvasRepository
from ..repositories.sqlite.sqlite_canvas_version_repository import SqliteCanvasVersionRepository
from ..repositories.sqlite.sqlite_chat_turn_repository import SqliteChatTurnRepository
from ..repositories.sqlite.sqlite_mood_board_repository import SqliteMoodBoardRepository
from ..repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from ..repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
from ..repositories.sqlite.sqlite_user_repository import SqliteUserRepository
from ..services.auth.auth_service import AuthService
from ..services.canvas.versioning_service import CanvasVersioningService
from ..services.knowledge.brand_dna_service import BrandDnaService
from ..services.knowledge.mood_board_service import MoodBoardService
from ..services.knowledge.product_dna_service import ProductDnaService
from ..services.orchestration.session_service import SessionService

DbSession = Annotated[AsyncSession, Depends(get_session)]

# The cookie name is defined once here — auth/routes.py sets it, this file reads it back, nothing
# else in the codebase should hardcode the literal string.
SESSION_COOKIE_NAME = "session_token"


def get_session_service(db: DbSession) -> SessionService:
    return SessionService(
        sessions=SqliteSessionRepository(db),
        canvas=SqliteCanvasRepository(db),
        versioning=CanvasVersioningService(
            canvas=SqliteCanvasRepository(db), versions=SqliteCanvasVersionRepository(db)
        ),
        chat_turns=SqliteChatTurnRepository(db),
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


def get_brand_repository(db: DbSession) -> SqliteBrandRepository:
    return SqliteBrandRepository(db)


def get_product_repository(db: DbSession) -> SqliteProductRepository:
    return SqliteProductRepository(db)


def get_user_repository(db: DbSession) -> SqliteUserRepository:
    return SqliteUserRepository(db)


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(users=SqliteUserRepository(db))


async def get_current_user(
    db: DbSession,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> UserModel:
    """Every route that requires login depends on this (Tasks_Workflows.md #1) — reads the signed
    cookie set by POST /auth/login, verifies it (stdlib HMAC, core/security.py), and loads the
    real user row. Raises `Unauthorized` (401) for a missing, malformed, tampered, or expired
    cookie, or one whose user_id no longer exists (e.g. deleted) — never a silent "guest" fallback,
    since every caller of this dependency genuinely requires a real logged-in user to proceed."""
    if session_token is None:
        raise Unauthorized()
    user_id = verify_session_token(session_token)
    if user_id is None:
        raise Unauthorized()
    user = await SqliteUserRepository(db).get_by_id(user_id)
    if user is None:
        raise Unauthorized()
    return user


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
CanvasRepositoryDep = Annotated[SqliteCanvasRepository, Depends(get_canvas_repository)]
CanvasVersionRepositoryDep = Annotated[SqliteCanvasVersionRepository, Depends(get_canvas_version_repository)]
SessionRepositoryDep = Annotated[SqliteSessionRepository, Depends(get_session_repository)]
VersioningServiceDep = Annotated[CanvasVersioningService, Depends(get_versioning_service)]
BrandDnaServiceDep = Annotated[BrandDnaService, Depends(get_brand_dna_service)]
ProductDnaServiceDep = Annotated[ProductDnaService, Depends(get_product_dna_service)]
BrandRepositoryDep = Annotated[SqliteBrandRepository, Depends(get_brand_repository)]
ProductRepositoryDep = Annotated[SqliteProductRepository, Depends(get_product_repository)]
MoodBoardServiceDep = Annotated[MoodBoardService, Depends(get_mood_board_service)]
UserRepositoryDep = Annotated[SqliteUserRepository, Depends(get_user_repository)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentUserDep = Annotated[UserModel, Depends(get_current_user)]
