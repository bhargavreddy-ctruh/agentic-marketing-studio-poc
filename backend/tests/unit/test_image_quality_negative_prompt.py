"""Regression tests for the image-fidelity negative-prompt baseline (Memory.md fidelity audit,
2026-10-05): every image-generation tool now applies a quality-floor exclusion list, merged with
(not replaced by) whatever the LLM itself supplies."""
from __future__ import annotations

from src.core.image_quality import (
    DEFAULT_NEGATIVE_PROMPT,
    append_quality_guard_to_prompt,
    merge_negative_prompt,
)


def test_merge_negative_prompt_uses_baseline_when_none_given() -> None:
    assert merge_negative_prompt(None) == DEFAULT_NEGATIVE_PROMPT
    assert merge_negative_prompt("") == DEFAULT_NEGATIVE_PROMPT


def test_merge_negative_prompt_appends_user_exclusions_without_dropping_baseline() -> None:
    merged = merge_negative_prompt("no text, no cartoon style")
    assert DEFAULT_NEGATIVE_PROMPT in merged
    assert "no text, no cartoon style" in merged


def test_append_quality_guard_to_prompt_folds_baseline_into_prompt_text() -> None:
    result = append_quality_guard_to_prompt("a red sneaker on a white background")
    assert result.startswith("a red sneaker on a white background")
    assert "Avoid:" in result
    assert DEFAULT_NEGATIVE_PROMPT in result
