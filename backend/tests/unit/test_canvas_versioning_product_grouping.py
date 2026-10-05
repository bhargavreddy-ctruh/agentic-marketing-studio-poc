"""Regression test for the Unassigned-grouping bug: editing an already-grouped element (e.g. an
S26 reference image) must carry `product_id`/`product_name`/`parent_element_id` forward onto the
edit result, not silently drop them (`services/canvas/versioning_service.py`'s
`_create_standalone_element`)."""
from __future__ import annotations

import uuid

import pytest

from src.core.config import settings
from src.models.canvas_element import CanvasElementModel
from src.models.canvas_element_version import CanvasElementVersionModel
from src.services.canvas.versioning_service import CanvasVersioningService


class _FakeCanvasRepo:
    def __init__(self) -> None:
        self.added: list[CanvasElementModel] = []

    async def add_element(self, element: CanvasElementModel) -> CanvasElementModel:
        self.added.append(element)
        return element

    async def get_element(self, element_id: str) -> CanvasElementModel | None:
        return next((e for e in self.added if e.id == element_id), None)

    async def list_for_session(self, session_id: str) -> list[CanvasElementModel]:
        return [e for e in self.added if e.session_id == session_id]

    async def update_element(self, element: CanvasElementModel) -> CanvasElementModel:
        return element


class _FakeVersionRepo:
    async def add(self, version: CanvasElementVersionModel) -> CanvasElementVersionModel:
        return version

    async def list_for_element(self, element_id: str) -> list[CanvasElementVersionModel]:
        return []

    async def delete_versions_after(self, element_id: str, version: int) -> None:
        return None


@pytest.mark.asyncio
async def test_standalone_edit_keeps_source_product_grouping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "canvas_versioning_enabled", False)
    svc = CanvasVersioningService(canvas=_FakeCanvasRepo(), versions=_FakeVersionRepo())

    source = CanvasElementModel(
        id=uuid.uuid4().hex,
        session_id="sess-1",
        element_type="image",
        produced_by_specialist="illustrator",
        storage_ref="storage://s26-reference.png",
        metadata_json={},
        product_id="prod-s26",
        product_name="Samsung Galaxy S26 Ultra",
    )

    result = await svc.apply_or_stage(
        source,
        storage_ref="storage://s26-edited.png",
        metadata={},
        action="direct_edit",
        approval_mode="auto",
    )

    assert result.product_id == "prod-s26"
    assert result.product_name == "Samsung Galaxy S26 Ultra"
    assert result.parent_element_id == source.id
    assert result.id != source.id
