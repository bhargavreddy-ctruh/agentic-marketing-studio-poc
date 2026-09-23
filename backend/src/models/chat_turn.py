"""
One real, persisted chat turn (2026-09-22) — a genuine fix for a long-disclosed gap: `SessionModel`
only ever stored the session's CURRENT `brief`/`status`/`next_prompt`, never a turn-by-turn message
log, so a page refresh could only restore "where the workflow currently stands," never the actual
conversation. A brand-new table, not an ALTER on an existing one — `Base.metadata.create_all()`
(models/base.py's `init_models()`) handles this with zero manual SQL, unlike every earlier column
addition this project needed (no migration framework — see that file's own docstring).
"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ChatTurnModel(Base, TimestampMixin):
    __tablename__ = "chat_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    user_text: Mapped[str] = mapped_column(Text)
    referenced_element_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # The real, accumulated "thinking" text streamed live during this turn (core/events.py's
    # `llm_delta` events, collected — previously discarded the instant the turn finished; see
    # `ChatPanel.tsx`'s old `withNarration`). Nullable: a turn can genuinely produce none (e.g.
    # `STREAM_LLM_THINKING_ENABLED=false`, or a provider that doesn't stream deltas).
    thinking_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The real final assistant-facing text for this turn (whatever `next_prompt.message` said, or
    # the honest "Generated — check the canvas" line) — enough to re-render the turn's own bubble
    # on restore without needing the full `SessionResponse` shape replayed.
    assistant_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Real, persisted Node Mode run history (2026-09-22, per an explicit user ask: "show all the
    # runs even after a refresh") — the complete ordered list of real node/specialist/tool events
    # this turn actually emitted (`core/events.py`'s `get_current_turn_events`), so Node Mode can be
    # rebuilt from the database on restore instead of only ever showing whatever arrived on the one
    # live SSE connection that happened to be open when it ran. `llm_delta` events are excluded (see
    # that file) — this is the run/pipeline structure, not a token-by-token replay.
    events_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
