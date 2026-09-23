"""
A real (test-grade) user — Tasks_Workflows.md #1. The first identity concept this project has had
at all; every session/brand was previously fully anonymous and globally scoped.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class UserModel(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(64))
    password_salt: Mapped[str] = mapped_column(String(32))
