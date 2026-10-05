"""Regression test (Part 7, fidelity audit 2026-10-05): chat-facing failure text must never leak
provider names, model ids, or environment-variable names — a real, live-found example used to show
the user "Provider 'replicate_llm' is unavailable: REPLICATE_API_TOKEN is not set" verbatim."""
from __future__ import annotations

from src.services.orchestration.graph import _user_safe_failure_message

_LEAK_TOKENS = ("replicate", "groq", "openrouter", "_TOKEN", "_API_KEY", "Provider '")


def test_failure_message_contains_no_internal_plumbing() -> None:
    message = _user_safe_failure_message()
    lowered = message.lower()
    for token in _LEAK_TOKENS:
        assert token.lower() not in lowered, f"leaked internal token {token!r} into user-facing text"
    assert "want to try again" in lowered
