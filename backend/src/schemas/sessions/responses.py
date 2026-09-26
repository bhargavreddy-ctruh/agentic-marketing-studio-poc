"""What a caller gets back for a session — never the raw SessionModel (mappers translate)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IdeationOption(BaseModel):
    """One pickable card option — Architecture.md section 1d: bold label + one-line rationale."""

    id: str
    label: str
    description: str


class IdeationPrompt(BaseModel):
    """The propose-options-plus-free-text shape, used for both ideation and the compliance gate."""

    message: str
    options: list[IdeationOption] = []
    allow_free_text: bool = True


class SessionResponse(BaseModel):
    id: str
    title: str
    status: str
    approval_mode: str
    guardrails_enabled: bool
    brief: dict
    # Real, live-found gap (2026-09-25): these two real DB columns were never exposed here at all,
    # so a frontend reading a session back had no way to tell WHICH brand/product was explicitly
    # linked — `GuardrailService._load_brand_and_products_json`'s explicit-link-else-fallback logic
    # is entirely server-side and invisible otherwise. `product_profile_id` is the single "most
    # recently touched" convenience column (also used for `product_photo_storage_ref` injection);
    # the real multi-product list lives in `brief["product_profile_ids"]`, already exposed via `brief`.
    brand_profile_id: str | None = None
    product_profile_id: str | None = None
    created_at: datetime
    updated_at: datetime
    next_prompt: IdeationPrompt | None = None


class ChatTurnResponse(BaseModel):
    """One real, persisted chat turn (2026-09-22) — the actual fix for the chat transcript never
    surviving a refresh. `thinking_text` is the real accumulated model text streamed live during
    this turn (None when nothing was ever streamed, e.g. thinking-streaming disabled)."""

    id: str
    user_text: str
    thinking_text: str | None
    assistant_text: str | None
    created_at: datetime
    # The real, complete node/specialist/tool event history for this turn (2026-09-22, per an
    # explicit user ask: "show all the runs even after a refresh") — `core/events.py`'s own real
    # events, minus `llm_delta` (see that file), so Node Mode can be rebuilt from the database on a
    # fresh page load instead of only ever showing whatever arrived on the one live SSE connection
    # that happened to be open at the time.
    events: list[dict] = []
    referenced_elements: list[dict] = []
