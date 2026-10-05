"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05): a canvas element's `verified_description` can be a multi-hundred-word
vision-model essay (`session_service.py`'s `_describe_uploaded_image`), and the full text used to
go out on every single canvas LIST fetch for every image element — real, measured payload bloat on
the hot, frequently-polled endpoint. Verifies: the list-response path truncates a long description
to a bounded length; a single-element fetch (`to_response`) keeps the full text untouched."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from src.mappers.canvas_mapper import _LIST_DESCRIPTION_MAX_CHARS, CanvasMapper
from src.models.canvas_element import CanvasElementModel

_NOW = datetime.now(UTC)


def _element(metadata_json: dict) -> CanvasElementModel:
    return CanvasElementModel(
        id=uuid.uuid4().hex, session_id="s1", element_type="image",
        produced_by_specialist="user_upload", storage_ref="", version=1,
        created_at=_NOW, updated_at=_NOW, compliance_status="disabled",
        metadata_json=metadata_json,
    )


def _element_with_long_description() -> CanvasElementModel:
    long_text = "A detailed promotional composite. " * 50  # well over the truncation cap
    return _element({"verified_description": long_text})


def test_list_response_truncates_long_description():
    element = _element_with_long_description()
    result = asyncio.run(CanvasMapper.to_state_response("s1", [element]))
    description = result.elements[0].description
    assert description is not None
    assert len(description) <= _LIST_DESCRIPTION_MAX_CHARS + 1  # +1 for the trailing ellipsis char
    assert description.endswith("…")


def test_single_element_response_keeps_full_description():
    element = _element_with_long_description()
    result = asyncio.run(CanvasMapper.to_response(element))
    assert result.description == (element.metadata_json or {})["verified_description"].strip()
    assert len(result.description) > _LIST_DESCRIPTION_MAX_CHARS


@pytest.mark.asyncio
async def test_short_description_is_unaffected_by_truncation():
    element = _element({"verified_description": "A short description."})
    result = await CanvasMapper.to_state_response("s1", [element])
    assert result.elements[0].description == "A short description."
