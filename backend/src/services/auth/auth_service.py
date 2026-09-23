"""
Auth service — Tasks_Workflows.md #1. Business logic for register/login lives here, not in the
route handler (Rules.md section 2). Depends on the UserRepository Protocol, never a concrete
SQLite class (Dependency Inversion).
"""
from __future__ import annotations

import uuid

from ...core.exceptions import Unauthorized, ValidationFailed
from ...core.security import create_session_token, hash_password, verify_password
from ...models.user import UserModel
from ...repositories.base import UserRepository


class AuthService:
    def __init__(self, users: UserRepository):
        self._users = users

    async def register(self, *, username: str, password: str) -> tuple[UserModel, str]:
        existing = await self._users.get_by_username(username)
        if existing is not None:
            raise ValidationFailed(f"Username '{username}' is already taken")
        password_hash, salt = hash_password(password)
        user = UserModel(
            id=uuid.uuid4().hex, username=username, password_hash=password_hash, password_salt=salt
        )
        user = await self._users.add(user)
        return user, create_session_token(user.id)

    async def login(self, *, username: str, password: str) -> tuple[UserModel, str]:
        user = await self._users.get_by_username(username)
        # Deliberately the same error message whether the username doesn't exist or the password
        # is wrong — never disclose which one was the actual problem (a real, if small, guardrail
        # against username enumeration, matching this project's "be careful about security
        # vulnerabilities" default even in a test-grade auth flow).
        if user is None or not verify_password(password, password_hash=user.password_hash, salt=user.password_salt):
            raise Unauthorized("Incorrect username or password")
        return user, create_session_token(user.id)

    async def get_by_id(self, user_id: str) -> UserModel | None:
        return await self._users.get_by_id(user_id)
