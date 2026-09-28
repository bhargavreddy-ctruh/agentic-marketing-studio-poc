"""
Runtime settings overrides (2026-09-28) — edited directly in Supabase's dashboard, not through
this app (no admin API/UI exists on purpose, per an explicit user decision). `key` matches a
`core/config.py` `Settings` field name (or the special-cased `cloudinary_url`); `value` is always
stored as plain text — the consuming side (`services/settings/settings_service.py`) is responsible
for coercing it to the right type (bool/str) for whichever field it's overriding.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class AppSettingModel(Base, TimestampMixin):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(2048))
