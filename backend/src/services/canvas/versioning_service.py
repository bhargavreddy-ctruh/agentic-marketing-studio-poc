"""
Per-element undo/redo — Architecture.md section 1c: "regenerating one clip or one background
shouldn't risk a manually-perfected neighboring element." Every real change to a canvas element
(direct-edit, targeted regenerate, comment-resolve) is recorded as a new version row here; undo/
redo move that ONE element's current-version pointer through its own history — never touching any
other element, since each element's versions are keyed to it alone.

Standard undo-then-edit semantics: undo/redo just move the pointer across already-recorded
versions (nothing is deleted); a genuinely NEW edit made while not at the latest version discards
the abandoned "future" versions first (`delete_versions_after`), rather than branching history.
"""
from __future__ import annotations

import uuid

from ...core.exceptions import NotFoundError, ValidationFailed
from ...models.canvas_element import CanvasElementModel
from ...models.canvas_element_version import CanvasElementVersionModel
from ...repositories.base import CanvasRepository, CanvasVersionRepository


class CanvasVersioningService:
    def __init__(self, canvas: CanvasRepository, versions: CanvasVersionRepository):
        self._canvas = canvas
        self._versions = versions

    async def _ensure_v1_recorded(self, element: CanvasElementModel) -> None:
        """An element's own creation is version 1 — recorded lazily, the first time anything
        touches this element's history, so elements created before this feature existed still
        have a real v1 row rather than a gap undo/redo could fall into."""
        existing = await self._versions.list_for_element(element.id)
        if not existing:
            await self._versions.add(CanvasElementVersionModel(
                id=uuid.uuid4().hex, element_id=element.id, version=1,
                storage_ref=element.storage_ref, metadata_json=element.metadata_json,
            ))

    async def record_new_version(
        self, element: CanvasElementModel, *, storage_ref: str, metadata: dict
    ) -> CanvasElementModel:
        """Call this whenever a real edit produces a new result for an element."""
        await self._ensure_v1_recorded(element)
        await self._versions.delete_versions_after(element.id, element.version)
        new_version_num = element.version + 1
        await self._versions.add(CanvasElementVersionModel(
            id=uuid.uuid4().hex, element_id=element.id, version=new_version_num,
            storage_ref=storage_ref, metadata_json=metadata,
        ))
        element.storage_ref = storage_ref
        element.metadata_json = metadata
        element.version = new_version_num
        return await self._canvas.update_element(element)

    async def undo(self, element_id: str) -> CanvasElementModel:
        element = await self._get_or_404(element_id)
        await self._ensure_v1_recorded(element)
        if element.version <= 1:
            raise ValidationFailed("Already at the earliest version — nothing to undo.")
        return await self._move_to(element, element.version - 1)

    async def redo(self, element_id: str) -> CanvasElementModel:
        element = await self._get_or_404(element_id)
        versions = await self._versions.list_for_element(element_id)
        max_version = max((v.version for v in versions), default=element.version)
        if element.version >= max_version:
            raise ValidationFailed("Already at the latest version — nothing to redo.")
        return await self._move_to(element, element.version + 1)

    async def list_versions(self, element_id: str) -> list[CanvasElementVersionModel]:
        element = await self._get_or_404(element_id)
        await self._ensure_v1_recorded(element)
        return await self._versions.list_for_element(element_id)

    async def apply_or_stage(
        self,
        element: CanvasElementModel,
        *,
        storage_ref: str,
        metadata: dict,
        action: str,
        approval_mode: str,
    ) -> CanvasElementModel:
        """The one real branch point between "auto" (apply immediately, existing behavior,
        unchanged) and "approve" (Memory.md, Phase 4: stage for explicit approval instead) — every
        edit-producing service (regenerate, comment resolution, direct-edit) calls this rather
        than deciding for itself, so the auto/approve distinction lives in exactly one place."""
        if approval_mode == "approve":
            return await self.stage_pending_edit(
                element, storage_ref=storage_ref, metadata=metadata, action=action
            )
        return await self.record_new_version(element, storage_ref=storage_ref, metadata=metadata)

    async def stage_pending_edit(
        self, element: CanvasElementModel, *, storage_ref: str, metadata: dict, action: str
    ) -> CanvasElementModel:
        """'approve' mode (Memory.md, Phase 4): a real edit result is held here, NOT applied, until
        the user explicitly approves or rejects it — the current version stays exactly as-is in
        the meantime. `action` records what produced it (regenerate/comment/direct_edit) purely
        for a human reading the pending state, not used by the code itself."""
        element.pending_storage_ref = storage_ref
        element.pending_metadata = metadata
        element.pending_action = action
        return await self._canvas.update_element(element)

    async def approve_pending_edit(self, element_id: str) -> CanvasElementModel:
        element = await self._get_or_404(element_id)
        if not element.pending_storage_ref:
            raise ValidationFailed("This element has no pending edit to approve.")
        storage_ref, metadata = element.pending_storage_ref, element.pending_metadata or {}
        element.pending_storage_ref = None
        element.pending_metadata = None
        element.pending_action = None
        return await self.record_new_version(element, storage_ref=storage_ref, metadata=metadata)

    async def reject_pending_edit(self, element_id: str) -> CanvasElementModel:
        """Discards the proposal — the current version is untouched, exactly as if the edit had
        never been requested."""
        element = await self._get_or_404(element_id)
        if not element.pending_storage_ref:
            raise ValidationFailed("This element has no pending edit to reject.")
        element.pending_storage_ref = None
        element.pending_metadata = None
        element.pending_action = None
        return await self._canvas.update_element(element)

    async def _move_to(self, element: CanvasElementModel, target_version: int) -> CanvasElementModel:
        versions = await self._versions.list_for_element(element.id)
        target = next((v for v in versions if v.version == target_version), None)
        if target is None:
            raise ValidationFailed(f"No recorded version {target_version} for this element.")
        element.storage_ref = target.storage_ref
        element.metadata_json = target.metadata_json
        element.version = target_version
        return await self._canvas.update_element(element)

    async def _get_or_404(self, element_id: str) -> CanvasElementModel:
        element = await self._canvas.get_element(element_id)
        if element is None:
            raise NotFoundError("CanvasElement", element_id)
        return element
