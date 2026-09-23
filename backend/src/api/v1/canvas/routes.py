"""
HTTP only — validate, call the service, return (Rules.md section 2). Phase 4 (Phases.md) adds the
three real Human-in-the-Loop intervention paths (Architecture.md section 1c): targeted regenerate,
comment resolution, and direct edit (via a real asset upload endpoint, since a direct edit's
actual pixel work happens client-side — this backend only needs to persist the result), plus real
per-element undo/redo.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response

from ....core.exceptions import NotFoundError, ValidationFailed
from ....core.local_storage import load_asset, save_asset
from ....core.mime_sniff import sniff_image_mime
from ....mappers.canvas_mapper import CanvasMapper
from ....models.canvas_element import CanvasElementModel
from ....schemas.canvas.requests import (
    CommentRequest,
    CreateElementRequest,
    DirectEditRequest,
    TargetedRegenerateRequest,
)
from ....schemas.canvas.responses import (
    AssetUploadResponse,
    CanvasElementResponse,
    CanvasElementVersionResponse,
    CanvasStateResponse,
)
from ....schemas.compliance.responses import ComplianceGateResponse
from ....services.canvas.comment_service import resolve_comment
from ....services.canvas.regenerate_service import regenerate_element
from ....services.compliance.compliance_gate import run_compliance_gate
from ...dependencies import (
    CanvasRepositoryDep,
    CanvasVersionRepositoryDep,
    SessionRepositoryDep,
    VersioningServiceDep,
)

router = APIRouter(prefix="/api/v1/canvas", tags=["canvas"])


@router.get("/{session_id}", response_model=CanvasStateResponse)
async def get_canvas_state(session_id: str, repo: CanvasRepositoryDep) -> CanvasStateResponse:
    elements = await repo.list_for_session(session_id)
    return CanvasMapper.to_state_response(session_id, elements)


def _element_type_from_mime(mime_type: str) -> str:
    for prefix, element_type in (("image/", "image"), ("video/", "video"), ("audio/", "audio")):
        if mime_type.startswith(prefix):
            return element_type
    raise ValidationFailed(f"Unsupported media type for a canvas element: {mime_type}")


@router.post("/{session_id}/elements", response_model=CanvasElementResponse)
async def create_element(
    session_id: str,
    body: CreateElementRequest,
    canvas: CanvasRepositoryDep,
    sessions: SessionRepositoryDep,
) -> CanvasElementResponse:
    """The real backend half of "Upload Media" / "New Image" / "New Video" / "New Audio" / "Paste"
    (right-click canvas menu, 2026-09-22) — places an already-uploaded asset (`POST /assets`)
    directly onto the canvas as a brand-new element, no specialist/model call involved. No auth
    dependency here, matching every other route in this router today (a real, pre-existing,
    disclosed gap — see Tasks_Workflows.md #3's note on canvas routes) rather than introducing
    inconsistent enforcement in one route alone."""
    session = await sessions.get(session_id)
    if session is None:
        raise NotFoundError("Session", session_id)
    loaded = load_asset(body.storage_ref)
    if loaded is None:
        raise NotFoundError("Asset", body.storage_ref)
    _, mime_type = loaded
    element = CanvasElementModel(
        id=uuid.uuid4().hex,
        session_id=session_id,
        element_type=_element_type_from_mime(mime_type),
        produced_by_specialist="user_upload",
        version=1,
        storage_ref=body.storage_ref,
        metadata_json={"source": "user_upload"},
        # Honest "not checked" — the compliance gate only ever runs on specialist-produced
        # elements (session_service.py's background task); a user's own upload was never claimed
        # to pass QA it was never actually put through.
        compliance_status="disabled",
    )
    created = await canvas.add_element(element)
    return CanvasMapper.to_response(created)


@router.get("/assets/{storage_ref}")
async def get_asset(storage_ref: str) -> Response:
    """Streams the real bytes behind a storage_ref (Phase 4b) — every other endpoint here returns
    metadata only (storage_ref strings), so this is the one route the frontend canvas actually
    loads pixels/video/audio from, e.g. `<img src="/api/v1/canvas/assets/{storage_ref}">`."""
    loaded = load_asset(storage_ref)
    if loaded is None:
        raise NotFoundError("Asset", storage_ref)
    data, mime_type = loaded
    return Response(content=data, media_type=mime_type)


@router.post("/elements/{element_id}/compliance", response_model=ComplianceGateResponse)
async def check_compliance(element_id: str, repo: CanvasRepositoryDep) -> ComplianceGateResponse:
    result = await run_compliance_gate(canvas=repo, element_id=element_id)
    return ComplianceGateResponse(**result)


@router.post("/assets", response_model=AssetUploadResponse)
async def upload_asset(file: UploadFile) -> AssetUploadResponse:
    """Persists a client-produced asset (e.g. a direct edit's resulting image, cropped/retouched
    in the browser) and returns its storage_ref — the seam between client-side pixel work and the
    server-side canvas element it gets attached to via PUT .../direct-edit below."""
    data = await file.read()
    mime = sniff_image_mime(data, file.content_type)
    storage_ref = save_asset(data, mime, metadata={"source": "direct_edit_upload"})
    return AssetUploadResponse(storage_ref=storage_ref, mime_type=mime)


@router.put("/elements/{element_id}/direct-edit", response_model=CanvasElementResponse)
async def direct_edit_element(
    element_id: str,
    body: DirectEditRequest,
    canvas: CanvasRepositoryDep,
    versioning: VersioningServiceDep,
    sessions: SessionRepositoryDep,
) -> CanvasElementResponse:
    """A direct edit — no model call (Architecture.md section 1c). The actual crop/retouch/recolor
    happens client-side; this records the already-uploaded result — applied immediately in "auto"
    mode, staged for explicit approval in "approve" mode (Memory.md, Phase 4)."""
    element = await canvas.get_element(element_id)
    if element is None:
        raise NotFoundError("CanvasElement", element_id)
    session = await sessions.get(element.session_id)
    updated = await versioning.apply_or_stage(
        element,
        storage_ref=body.storage_ref,
        metadata={**element.metadata_json, "direct_edit": True},
        action="direct_edit",
        approval_mode=session.approval_mode if session else "auto",
    )
    return CanvasMapper.to_response(updated)


@router.post("/elements/{element_id}/regenerate", response_model=CanvasElementResponse)
async def targeted_regenerate(
    element_id: str,
    body: TargetedRegenerateRequest,
    canvas: CanvasRepositoryDep,
    versions: CanvasVersionRepositoryDep,
    sessions: SessionRepositoryDep,
) -> CanvasElementResponse:
    """Targeted regenerate — invokes exactly the ONE specialist that produced this element, not
    the whole Lead (Architecture.md section 1c)."""
    updated = await regenerate_element(
        canvas=canvas, versions=versions, sessions=sessions, element_id=element_id, instruction=body.instruction
    )
    return CanvasMapper.to_response(updated)


@router.post("/elements/{element_id}/comments", response_model=CanvasElementResponse)
async def comment_on_element(
    element_id: str,
    body: CommentRequest,
    canvas: CanvasRepositoryDep,
    versions: CanvasVersionRepositoryDep,
    sessions: SessionRepositoryDep,
) -> CanvasElementResponse:
    """A comment — resolved into a scoped instruction against whichever specialist the comment's
    real content matches, not necessarily the one that originally produced the element
    (Architecture.md section 1c)."""
    updated = await resolve_comment(
        canvas=canvas, versions=versions, sessions=sessions, element_id=element_id, comment=body.text
    )
    return CanvasMapper.to_response(updated)


@router.post("/elements/{element_id}/approve-edit", response_model=CanvasElementResponse)
async def approve_pending_edit(element_id: str, versioning: VersioningServiceDep) -> CanvasElementResponse:
    """"approve" mode only (Memory.md, Phase 4): commits a staged regenerate/comment/direct-edit
    result as the new current version. A no-op error if there's nothing pending."""
    updated = await versioning.approve_pending_edit(element_id)
    return CanvasMapper.to_response(updated)


@router.post("/elements/{element_id}/reject-edit", response_model=CanvasElementResponse)
async def reject_pending_edit(element_id: str, versioning: VersioningServiceDep) -> CanvasElementResponse:
    """"approve" mode only: discards a staged edit — the current version is untouched."""
    updated = await versioning.reject_pending_edit(element_id)
    return CanvasMapper.to_response(updated)


@router.post("/elements/{element_id}/undo", response_model=CanvasElementResponse)
async def undo_element(element_id: str, versioning: VersioningServiceDep) -> CanvasElementResponse:
    updated = await versioning.undo(element_id)
    return CanvasMapper.to_response(updated)


@router.post("/elements/{element_id}/redo", response_model=CanvasElementResponse)
async def redo_element(element_id: str, versioning: VersioningServiceDep) -> CanvasElementResponse:
    updated = await versioning.redo(element_id)
    return CanvasMapper.to_response(updated)


@router.get("/elements/{element_id}/versions", response_model=list[CanvasElementVersionResponse])
async def list_element_versions(
    element_id: str, versioning: VersioningServiceDep
) -> list[CanvasElementVersionResponse]:
    versions = await versioning.list_versions(element_id)
    return [
        CanvasElementVersionResponse(version=v.version, storage_ref=v.storage_ref, created_at=v.created_at)
        for v in versions
    ]
