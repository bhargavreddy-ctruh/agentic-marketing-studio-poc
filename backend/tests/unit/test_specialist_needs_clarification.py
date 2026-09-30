"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found gap (2026-09-30, explicit user ask: "don't just assume, make agents ask
questions when there's genuine doubt"): the MASTER_DIRECTIVE already told specialists to "ask a
clarifying question... wait for a response," but nothing downstream recognized that as anything
but a crash — only the single-key {"error": ...} shape was ever recognized, routed through
SpecialistFailed, indistinguishable from a real provider outage. Verifies the new, real signal:
a specialist returning {"question": ..., "options": [...]} raises SpecialistNeedsClarification
(carrying the real question/options), never SpecialistFailed.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import SpecialistNeedsClarification
from src.providers.llm.base import LLMResult
from src.services.specialists.registry import load_all_specialists
from src.services.specialists.runner import run_specialist_agentic
from src.services.tools.registry import load_all_tools

load_all_specialists()
load_all_tools()


def _fake_llm(*responses: str) -> AsyncMock:
    provider = AsyncMock()
    provider.complete.side_effect = [LLMResult(text=r, model="fake-model") for r in responses]
    return provider


@pytest.mark.asyncio
async def test_question_shape_raises_needs_clarification_not_failed():
    with patch(
        "src.services.specialists.runner.get_llm_provider",
        return_value=_fake_llm(
            '{"question": "Which linked product is this about — the 2a or the 4a?", '
            '"options": [{"id": "p1", "label": "2a", "description": "Nothing Phone (2a)"}, '
            '{"id": "p2", "label": "4a", "description": "Nothing Phone 4a"}]}'
        ),
    ):
        with pytest.raises(SpecialistNeedsClarification) as excinfo:
            await run_specialist_agentic("reference_curator", context="find references")

    exc = excinfo.value
    assert exc.question == "Which linked product is this about — the 2a or the 4a?"
    assert exc.options == [
        {"id": "p1", "label": "2a", "description": "Nothing Phone (2a)"},
        {"id": "p2", "label": "4a", "description": "Nothing Phone 4a"},
    ]


@pytest.mark.asyncio
async def test_question_without_options_is_still_recognized():
    with patch(
        "src.services.specialists.runner.get_llm_provider",
        return_value=_fake_llm('{"question": "What text should the headline say?"}'),
    ):
        with pytest.raises(SpecialistNeedsClarification) as excinfo:
            await run_specialist_agentic("reference_curator", context="find references")

    assert excinfo.value.question == "What text should the headline say?"
    assert excinfo.value.options is None
