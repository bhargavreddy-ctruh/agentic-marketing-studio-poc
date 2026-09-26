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

from ...core.config import settings
from ...core.exceptions import NotFoundError, ValidationFailed
from ...core.middleware.logging import get_logger
from ...models.canvas_element import CanvasElementModel
from ...models.canvas_element_version import CanvasElementVersionModel
from ...repositories.base import CanvasRepository, CanvasVersionRepository

log = get_logger(__name__)


class CanvasVersioningService:
    def __init__(self, canvas: CanvasRepository, versions: CanvasVersionRepository):
        self._canvas = canvas
        self._versions = versions

    async def _ensure_v1_recorded(self, element: CanvasElementModel) -> None:
        """An element's own creation is version 1 — recorded lazily, the first time anything
        touches this element's history, so elements created before this feature existed still
        have a real v1 row rather than a gap undo/redo could fall into. Records the element's
        CURRENT `element_type` as a best-effort backfill for pre-existing elements — real for any
        element that hasn't changed type yet (the overwhelming majority), an honest approximation
        only for one that had already been mislabeled by the pre-2026-09-21 bug before ever
        reaching this method."""
        existing = await self._versions.list_for_element(element.id)
        if not existing:
            await self._versions.add(CanvasElementVersionModel(
                id=uuid.uuid4().hex, element_id=element.id, version=1,
                storage_ref=element.storage_ref, metadata_json=element.metadata_json,
                element_type=element.element_type,
            ))

    async def record_new_version(
        self,
        element: CanvasElementModel,
        *,
        storage_ref: str,
        metadata: dict,
        element_type: str | None = None,
    ) -> CanvasElementModel:
        """Call this whenever a real edit produces a new result for an element. `element_type`
        only needs passing when the edit genuinely changes what KIND of asset this is (real,
        live-found case: a direct_fix's `video_stitcher` turning a still-image element into a
        video one) — defaults to the element's current type, unchanged, for every other edit
        path (direct-edit, regenerate, comment), none of which ever change an element's kind."""
        await self._ensure_v1_recorded(element)
        # Anchor on `element.version` when a real row for it actually exists — this is what makes
        # "undo, then make a new edit" correctly discard the abandoned future versions (delete
        # everything after the version the user is actually AT). Falls back to the highest
        # recorded version only when `element.version` itself has no matching row — the real,
        # live-found drift case (2026-09-21): a chat-driven direct_fix used to bump `element.version`
        # by hand with no row ever written here, so an element edited via chat before that fix
        # landed can have a counter ahead of what its history actually contains. A real regression
        # was caught here on independent review (2026-09-22): anchoring on the highest recorded
        # version UNCONDITIONALLY (not just for the drift case) made `delete_versions_after` a
        # no-op by definition, silently breaking the truncation — undo to v1 then a new edit would
        # leave v2-v5's abandoned future intact and create a v6, instead of discarding them.
        existing = await self._versions.list_for_element(element.id)
        existing_versions = {v.version for v in existing}
        anchor = element.version if element.version in existing_versions else max(existing_versions, default=element.version)
        await self._versions.delete_versions_after(element.id, anchor)
        new_version_num = anchor + 1
        resolved_type = element_type or element.element_type
        await self._versions.add(CanvasElementVersionModel(
            id=uuid.uuid4().hex, element_id=element.id, version=new_version_num,
            storage_ref=storage_ref, metadata_json=metadata, element_type=resolved_type,
        ))
        element.storage_ref = storage_ref
        element.metadata_json = metadata
        element.version = new_version_num
        element.element_type = resolved_type
        return await self._canvas.update_element(element)

    async def undo(self, element_id: str) -> CanvasElementModel:
        element = await self._get_or_404(element_id)
        await self._ensure_v1_recorded(element)
        # Step to the nearest REAL recorded version below the current one, not a naive
        # `version - 1` — an element edited via chat before 2026-09-21's fix can have a `version`
        # counter ahead of what its history actually contains (see record_new_version's own
        # comment), so `version - 1` can point at a version number with no row at all.
        versions = sorted(v.version for v in await self._versions.list_for_element(element_id))
        earlier = [v for v in versions if v < element.version]
        if not earlier:
            raise ValidationFailed("Already at the earliest version — nothing to undo.")
        return await self._move_to(element, earlier[-1])

    async def redo(self, element_id: str) -> CanvasElementModel:
        element = await self._get_or_404(element_id)
        versions = sorted(v.version for v in await self._versions.list_for_element(element_id))
        later = [v for v in versions if v > element.version]
        if not later:
            raise ValidationFailed("Already at the latest version — nothing to redo.")
        return await self._move_to(element, later[0])

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
        element_type: str | None = None,
    ) -> CanvasElementModel:
        """The one real branch point between "auto" (apply immediately, existing behavior,
        unchanged) and "approve" (Memory.md, Phase 4: stage for explicit approval instead) — every
        edit-producing service (regenerate, comment resolution, direct-edit) calls this rather
        than deciding for itself, so the auto/approve distinction lives in exactly one place.
        `element_type` — see `record_new_version`'s own docstring — is NOT yet threaded through
        "approve" mode's staging (`CanvasElementModel` has no `pending_element_type` field): a
        genuinely disclosed, narrower gap than the one this fixes, since no edit path that changes
        an element's kind (currently only chat-driven direct_fix) is used under "approve" mode in
        practice yet."""
        if not settings.canvas_versioning_enabled:
            # Real, live-found user ask (2026-09-24): "every generated element should be displayed
            # on canvas as individual element, detach the element versioning for now." Applied
            # immediately regardless of `approval_mode` — see `core/config.py`'s
            # `canvas_versioning_enabled` for the disclosed reason staging doesn't apply here.
            return await self._create_standalone_element(
                element, storage_ref=storage_ref, metadata=metadata, element_type=element_type
            )
        if approval_mode == "approve":
            return await self.stage_pending_edit(
                element, storage_ref=storage_ref, metadata=metadata, action=action
            )
        return await self.record_new_version(
            element, storage_ref=storage_ref, metadata=metadata, element_type=element_type
        )

    async def _create_standalone_element(
        self,
        element: CanvasElementModel,
        *,
        storage_ref: str,
        metadata: dict,
        element_type: str | None = None,
    ) -> CanvasElementModel:
        """The `canvas_versioning_enabled=False` path — same real session/specialist/kind the
        source element already carries, but a genuinely NEW, independent element (its own id,
        v1, no version history linking it back), never a mutation of `element` itself. `element`
        is left completely untouched on disk."""
        new_element = CanvasElementModel(
            id=uuid.uuid4().hex,
            session_id=element.session_id,
            element_type=element_type or element.element_type,
            produced_by_specialist=element.produced_by_specialist,
            storage_ref=storage_ref,
            metadata_json=metadata,
        )
        created = await self._canvas.add_element(new_element)
        log.info(
            "canvas_versioning_detached_new_element",
            extra={"_extra_source_element": element.id, "_extra_new_element": created.id},
        )
        return created

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
        # Restores the real type recorded for THAT version, not just its storage_ref — an element
        # that changed kind mid-history (e.g. a direct_fix turning a still image into a video)
        # must render correctly at every version undo/redo can land on, not just its latest one.
        element.element_type = target.element_type
        return await self._canvas.update_element(element)

    async def _get_or_404(self, element_id: str) -> CanvasElementModel:
        element = await self._canvas.get_element(element_id)
        if element is None:
            raise NotFoundError("CanvasElement", element_id)
        return element
