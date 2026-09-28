"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Verifies the code-enforced output contract added to `runner.py`'s `run_specialist_agentic`
(2026-09-26, decomposition-quality investigation): a specialist's final JSON missing one of its
own declared `required_output_fields` (`specialists/registry.py`) gets ONE bounded corrective
retry before failing for real — the same shape as the existing JSON-parse-failure retry, not a
new mechanism.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import SpecialistFailed
from src.providers.llm.base import LLMResult
from src.services.specialists.registry import load_all_specialists
from src.services.specialists.runner import run_specialist_agentic
from src.services.tools.registry import load_all_tools

load_all_specialists()
load_all_tools()


def _fake_llm(*responses: str) -> AsyncMock:
    provider = AsyncMock()
    provider.complete.side_effect = [
        LLMResult(text=r, model="fake-model") for r in responses
    ]
    return provider


@pytest.mark.asyncio
async def test_missing_required_field_triggers_one_bounded_retry_then_succeeds():
    # reference_curator requires ("reference_summary",) — first reply omits it, second supplies it.
    with patch(
        "src.services.specialists.runner.get_llm_provider",
        return_value=_fake_llm(
            '{"foo": "bar"}',
            '{"reference_summary": "a real summary of gathered references"}',
        ),
    ):
        result = await run_specialist_agentic("reference_curator", context="find references")
    assert result.data["reference_summary"] == "a real summary of gathered references"


@pytest.mark.asyncio
async def test_missing_required_field_fails_after_retry_exhausted():
    # Both replies omit the required field — must raise SpecialistFailed, not silently succeed.
    with patch(
        "src.services.specialists.runner.get_llm_provider",
        return_value=_fake_llm('{"foo": "bar"}', '{"baz": "qux"}'),
    ), pytest.raises(SpecialistFailed):
        await run_specialist_agentic("reference_curator", context="find references")


@pytest.mark.asyncio
async def test_error_escape_hatch_is_never_treated_as_missing_fields():
    # A genuine {"error": "..."} response must raise SpecialistFailed immediately (the model
    # correctly declining), never get retried as if fields were merely missing.
    with patch(
        "src.services.specialists.runner.get_llm_provider",
        return_value=_fake_llm('{"error": "no real reference material was found"}'),
    ), pytest.raises(SpecialistFailed):
        await run_specialist_agentic("reference_curator", context="find references")
