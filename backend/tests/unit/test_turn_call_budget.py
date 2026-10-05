"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Part 8 fidelity audit (2026-10-05): a turn-level specialist-call budget stops a pathological loop
(a specialist retried indefinitely, or re-invoked repeatedly across resumed turns) from silently
burning real LLM/provider spend with no circuit breaker. Verifies: unset (the default) is
unbounded/unaffected; once set, the budget is consumed per real specialist call and raises
SpecialistFailed cleanly when exhausted — never a silent infinite loop."""
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import SpecialistFailed
from src.providers.llm.base import LLMResult
from src.services.specialists.registry import load_all_specialists
from src.services.specialists.runner import run_specialist_agentic, start_turn_budget
from src.services.tools.registry import load_all_tools

load_all_specialists()
load_all_tools()


def _fake_llm(*responses: str) -> AsyncMock:
    provider = AsyncMock()
    provider.complete.side_effect = [LLMResult(text=r, model="fake-model") for r in responses]
    return provider


@pytest.mark.asyncio
async def test_no_budget_set_is_unaffected() -> None:
    with patch(
        "src.services.specialists.runner.get_llm_provider",
        return_value=_fake_llm('{"reference_summary": "a summary", "text_card_storage_ref": "storage://card.txt"}'),
    ):
        result = await run_specialist_agentic("reference_curator", context="find references")
    assert result.data["reference_summary"] == "a summary"


@pytest.mark.asyncio
async def test_budget_exhausted_raises_specialist_failed() -> None:
    start_turn_budget(1)
    try:
        with patch(
            "src.services.specialists.runner.get_llm_provider",
            return_value=_fake_llm('{"reference_summary": "a summary", "text_card_storage_ref": "storage://card.txt"}'),
        ):
            # First call consumes the one unit of budget and succeeds.
            await run_specialist_agentic("reference_curator", context="find references")
            # Second call has none left — must fail cleanly, not loop/hang.
            with pytest.raises(SpecialistFailed, match="budget"):
                await run_specialist_agentic("reference_curator", context="find references")
    finally:
        # Reset to "no budget set" so this test's state can never leak into any other test that
        # happens to share the ambient contextvars context (test isolation, not a product concern).
        start_turn_budget(-1)
