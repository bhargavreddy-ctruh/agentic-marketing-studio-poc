"""
Product DNA — the exact fields the guardrail-first pattern binds into templates instead of
letting a model free-generate them (Architecture.md section 1: price, discount, product facts).
"""
from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ProductProfileModel(Base, TimestampMixin):
    __tablename__ = "product_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # mustShow / neverShow / claimsAllowed / claimsDisallowed / labelVisibility, price, discount —
    # the exact shape run_product_intelligence already produces (Rules.md section 6).
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    photo_storage_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    indexed: Mapped[bool] = mapped_column(default=False)
