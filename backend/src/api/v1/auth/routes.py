"""
HTTP only — validate, call the service, return (Rules.md section 2). Tasks_Workflows.md #1: a
real (test-grade) login flow — one signed, HttpOnly cookie, no server-side session table, no email
verification/reset/OAuth (out of scope by explicit design, not an oversight).
"""
from __future__ import annotations

from fastapi import APIRouter, Response

from ....core.config import settings
from ....schemas.auth.requests import LoginRequest, RegisterRequest
from ....schemas.auth.responses import UserResponse
from ...dependencies import SESSION_COOKIE_NAME, AuthServiceDep, CurrentUserDep

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# localhost:3000 and localhost:8000 are different ORIGINS (CORS) but the same SITE (SameSite
# cookie rules key off the registrable domain, not the port) — Lax is sent correctly on a
# cross-origin `fetch(..., {credentials: "include"})` between them, so no Secure/None dance is
# needed for local dev. A real (non-localhost) deployment would need Secure + a real HTTPS origin.
_COOKIE_KWARGS = {"httponly": True, "samesite": "lax", "secure": False, "path": "/"}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=settings.auth_session_ttl_seconds, **_COOKIE_KWARGS
    )


@router.post("/register", response_model=UserResponse)
async def register(body: RegisterRequest, svc: AuthServiceDep, response: Response) -> UserResponse:
    user, token = await svc.register(username=body.username, password=body.password)
    _set_session_cookie(response, token)
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at)


@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, svc: AuthServiceDep, response: Response) -> UserResponse:
    user, token = await svc.login(username=body.username, password=body.password)
    _set_session_cookie(response, token)
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse(id=current_user.id, username=current_user.username, created_at=current_user.created_at)
