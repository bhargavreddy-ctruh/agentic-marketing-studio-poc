"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found bug (2026-10-07, Monster Energy "plan said 1:1, got 16:9" complaint): the
deterministic aspect-ratio hint (`deliverable_hint_block`) used to be computed ONCE per turn, from
the whole turn's message, and copied unchanged into every dynamic-plan step's context — so a step
whose own instruction said "1:1" still got a "set aspect_ratio to 16:9" hint if some OTHER step in
the same plan named a 16:9 deliverable. Verifies: each step's hint is scoped to THAT step's own
instruction only.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.orchestration.graph import _dynamic_executor_node
from src.services.specialists.registry import load_all_specialists
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord

load_all_specialists()

_PLAN = [
    {"specialist": "illustrator", "instruction": "generate a 1:1 square Instagram post of the product"},
    {"specialist": "composition_artist", "instruction": "generate a 16:9 youtube thumbnail version"},
]


def _fake_result(specialist_name: str, storage_ref: str) -> AgenticStepResult:
    return AgenticStepResult(
        specialist_name=specialist_name,
        model="fake-model",
        data={"image_prompt": f"{specialist_name} prompt", "aspect_ratio": "1:1", "brand_facts_used": ""},
        tool_calls=[ToolCallRecord(tool_name="base_image_generator", args={}, ok=True, data={"storage_ref": storage_ref})],
    )


@pytest.mark.asyncio
async def test_each_steps_aspect_ratio_hint_reflects_only_its_own_instruction():
    seen_contexts: dict[str, str] = {}

    async def _run_review(specialist, *, context, needs_retry, reminder, brief=None):
        joined = " ".join(p.get("text", "") for p in context if isinstance(p, dict))
        seen_contexts[specialist] = joined
        return _fake_result(specialist, f"ref-{specialist}")

    state = {
        "dynamic_plan": _PLAN,
        "user_message": "make a 1:1 square Instagram post, and also a 16:9 youtube thumbnail version",
        "brief": {"idea": "make a 1:1 square Instagram post, and also a 16:9 youtube thumbnail version"},
    }
    with patch("src.services.orchestration.graph.run_specialist_with_review", new=AsyncMock(side_effect=_run_review)):
        await _dynamic_executor_node(state)

    illustrator_context = seen_contexts["illustrator"]
    composition_context = seen_contexts["composition_artist"]

    assert 'aspect_ratio to "16:9"' not in illustrator_context
    assert 'aspect_ratio to "9:16"' not in illustrator_context
    assert 'aspect_ratio to "1:1"' not in composition_context
