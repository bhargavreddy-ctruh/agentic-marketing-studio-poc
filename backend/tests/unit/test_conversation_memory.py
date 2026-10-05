"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Tiered conversation memory (2026-10-05, explicit user design: Ledger/Window/Digests/Recall/
caching — see `conversation_memory.py`'s own module docstring). Verifies: the window respects both
a message-count and a token-count budget (whichever hits first), older turns inside the window get
masked to bounded text while the most recent stay full, the Ledger is a pure function of the real
canvas elements (never LLM-derived), and digest open/close/merge bookkeeping is correct — including
the real bug this test suite catches: an early version cached pending turn OBJECTS under a key that
was never actually written back (`_pending_turns_cache` vs `_pending_turns`), silently losing every
turn from an earlier call once a digest finally closed."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.models.canvas_element import CanvasElementModel
from src.models.chat_turn import ChatTurnModel
from src.services.knowledge.conversation_memory import (
    build_ledger,
    build_window,
    digests_block,
    ledger_block,
    merge_digests,
    summarize_turns_to_digest,
    update_digests,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _turn(i: int, user: str = "", assistant: str = "") -> ChatTurnModel:
    return ChatTurnModel(
        id=f"turn-{i}",
        session_id="s1",
        user_text=user or f"message {i}",
        assistant_text=assistant or f"response {i}",
        created_at=_NOW + timedelta(seconds=i),
    )


def _element(i: int, **kwargs) -> CanvasElementModel:
    defaults = {
        "id": f"el-{i}", "session_id": "s1", "element_type": "image",
        "produced_by_specialist": "illustrator", "version": 1, "created_at": _NOW,
        "updated_at": _NOW, "compliance_status": "passed",
    }
    defaults.update(kwargs)
    return CanvasElementModel(**defaults)


# --- build_window ---------------------------------------------------------------------------

def test_window_respects_max_messages():
    turns = [_turn(i) for i in range(20)]
    window, dropped = build_window(turns, max_messages=5, max_tokens=100000)
    assert len(window) == 5
    assert len(dropped) == 15
    # oldest-first in the window, i.e. the LAST 5 turns, in order
    assert [w["user"] for w in window] == [f"message {i}" for i in range(15, 20)]


def test_window_respects_max_tokens_before_max_messages():
    long_text = "x" * 4000  # ~1000 tokens at the 4-chars-per-token estimate
    turns = [_turn(i, user=long_text, assistant=long_text) for i in range(10)]
    window, dropped = build_window(turns, max_messages=10, max_tokens=2500)
    # Each turn is ~2000 tokens (user+assistant); budget of 2500 allows only 1 before the 2nd would
    # exceed it (masking truncates older turns to 300 chars, but the recent ones stay full-size).
    assert len(window) >= 1
    assert len(window) < 10
    assert len(dropped) == 10 - len(window)


def test_window_masks_older_turns_but_keeps_recent_full():
    long_text = "A sentence. " * 50  # well over the 300-char truncation cap
    turns = [_turn(i, user=long_text, assistant=long_text) for i in range(5)]
    window, _ = build_window(turns, max_messages=5, max_tokens=100000)
    # Most recent 2 (index -1, -2 of the original list => the last two window entries) stay full.
    assert window[-1]["user"] == long_text
    assert window[-2]["user"] == long_text
    # Older ones are truncated.
    assert window[0]["user"] != long_text
    assert len(window[0]["user"]) < len(long_text)
    assert window[0]["user"].endswith("…")


def test_window_dropped_is_oldest_first_and_excludes_kept_turns():
    turns = [_turn(i) for i in range(6)]
    window, dropped = build_window(turns, max_messages=3, max_tokens=100000)
    assert [t.id for t in dropped] == ["turn-0", "turn-1", "turn-2"]
    kept_ids = {t["user"] for t in window}
    assert "message 3" in kept_ids and "message 4" in kept_ids and "message 5" in kept_ids


# --- build_ledger / ledger_block --------------------------------------------------------------

def test_ledger_is_deterministic_pure_function():
    elements = [_element(1, product_name="Nothing Phone"), _element(2, element_type="video")]
    a = build_ledger(elements, current_focus_id="el-2")
    b = build_ledger(elements, current_focus_id="el-2")
    assert a == b
    assert a["current_focus"] == "el-2"
    assert set(a["artifacts"].keys()) == {"el-1", "el-2"}
    assert a["artifacts"]["el-1"]["label"] == "Nothing Phone"


def test_ledger_block_empty_on_no_artifacts():
    assert ledger_block(None) == ""
    assert ledger_block({"artifacts": {}, "current_focus": None}) == ""


def test_ledger_block_previews_at_most_five_with_overflow_note():
    elements = [_element(i) for i in range(8)]
    ledger = build_ledger(elements, current_focus_id=None)
    block = ledger_block(ledger)
    assert "8 artifact(s)" in block
    assert "...and 3 more." in block


# --- update_digests ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_digest_closes_once_threshold_reached_and_summarizes_real_turns():
    turns = [_turn(i, user=f"price is {100 + i}") for i in range(10)]
    with patch(
        "src.services.knowledge.conversation_memory.summarize_turns_to_digest",
        new=AsyncMock(return_value="summary of 10 turns"),
    ) as mock_summarize:
        digests = await update_digests([], turns, digest_turn_threshold=10)

    assert len(digests) == 1
    assert digests[0]["summary"] == "summary of 10 turns"
    assert digests[0]["open"] is False
    assert digests[0]["covered_turn_ids"] == [t.id for t in turns]
    # The real regression check: the digest must be summarized from EVERY covered turn, not just
    # whichever batch happened to be passed in the call that crossed the threshold.
    (passed_turns,), _ = mock_summarize.call_args
    assert [t.id for t in passed_turns] == [t.id for t in turns]


@pytest.mark.asyncio
async def test_digest_stays_open_below_threshold_and_accumulates_across_calls():
    first_batch = [_turn(i) for i in range(4)]
    digests = await update_digests([], first_batch, digest_turn_threshold=10)
    assert len(digests) == 1
    assert digests[0]["open"] is True
    assert digests[0]["covered_turn_ids"] == [t.id for t in first_batch]

    # Second call: `dropped_turns` is the FULL out-of-window history again (re-derived fresh each
    # turn, never an incremental delta) — includes the first batch plus new turns.
    second_batch = first_batch + [_turn(i) for i in range(4, 7)]
    digests = await update_digests(digests, second_batch, digest_turn_threshold=10)
    assert len(digests) == 1
    assert digests[0]["open"] is True
    assert digests[0]["covered_turn_ids"] == [t.id for t in second_batch]


@pytest.mark.asyncio
async def test_digest_does_not_double_count_already_covered_turns():
    turns = [_turn(i) for i in range(5)]
    existing = [{"epoch": 0, "summary": "earlier span", "covered_turn_ids": [t.id for t in turns[:3]], "open": False}]
    # dropped_turns includes turns already covered by the closed digest plus 2 new ones.
    digests = await update_digests(existing, turns, digest_turn_threshold=10)
    new_open = next(d for d in digests if d.get("open"))
    assert new_open["covered_turn_ids"] == [turns[3].id, turns[4].id]


@pytest.mark.asyncio
async def test_merge_cap_merges_oldest_closed_digests():
    closed = [
        {"epoch": i, "summary": f"summary {i}", "covered_turn_ids": [f"t{i}"], "open": False}
        for i in range(4)
    ]
    with patch(
        "src.services.knowledge.conversation_memory.merge_digests",
        new=AsyncMock(return_value="merged epoch summary"),
    ):
        digests = await update_digests(closed, [], merge_cap=3)

    assert len(digests) == 3  # merged down to the cap
    assert digests[0]["summary"] == "merged epoch summary"
    assert digests[0]["covered_turn_ids"] == ["t0", "t1"]
    assert digests[1]["summary"] == "summary 2"
    assert digests[2]["summary"] == "summary 3"


# --- digests_block ------------------------------------------------------------------------------

def test_digests_block_empty_when_none_closed():
    assert digests_block(None) == ""
    assert digests_block([{"epoch": 0, "summary": "", "open": True, "covered_turn_ids": []}]) == ""


def test_digests_block_renders_closed_summaries_oldest_first():
    digests = [
        {"epoch": 0, "summary": "first span", "open": False, "covered_turn_ids": []},
        {"epoch": 1, "summary": "second span", "open": False, "covered_turn_ids": []},
    ]
    block = digests_block(digests)
    assert block.index("first span") < block.index("second span")


# --- summarize_turns_to_digest / merge_digests: LLM mocked, failure fallback ------------------

@pytest.mark.asyncio
async def test_summarize_turns_to_digest_calls_llm_and_returns_text():
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=type("R", (), {"text": "  a real summary  "})())
    with patch("src.services.knowledge.conversation_memory.get_llm_provider", return_value=provider):
        summary = await summarize_turns_to_digest([_turn(0)])
    assert summary == "a real summary"


@pytest.mark.asyncio
async def test_summarize_turns_to_digest_degrades_honestly_on_llm_failure():
    provider = AsyncMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch("src.services.knowledge.conversation_memory.get_llm_provider", return_value=provider):
        summary = await summarize_turns_to_digest([_turn(0, user="hello there")])
    assert "1 earlier turns" in summary
    assert "hello there" in summary


@pytest.mark.asyncio
async def test_merge_digests_single_summary_passthrough_no_llm_call():
    with patch("src.services.knowledge.conversation_memory.get_llm_provider") as mock_get:
        result = await merge_digests(["only one"])
    assert result == "only one"
    mock_get.assert_not_called()
