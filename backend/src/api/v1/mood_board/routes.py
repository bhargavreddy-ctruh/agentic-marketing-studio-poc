"""
HTTP only — validate, call the service, return (Rules.md section 2). Lets a real prior-campaign
asset be uploaded so `asset_mood_board_search` (Architecture.md section 1b) has something real to
search — before this, the tool always correctly returned "not configured" since no internal asset
library existed at all.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, UploadFile

from ....core.mime_sniff import sniff_image_mime
from ....mappers.mood_board_mapper import MoodBoardMapper
from ....schemas.mood_board.responses import MoodBoardAssetResponse
from ...dependencies import MoodBoardServiceDep

router = APIRouter(prefix="/api/v1/mood-board", tags=["mood-board"])


@router.post("/assets", response_model=MoodBoardAssetResponse)
async def upload_mood_board_asset(
    service: MoodBoardServiceDep, file: UploadFile, description: str = Form(...)
) -> MoodBoardAssetResponse:
    data = await file.read()
    mime = sniff_image_mime(data, file.content_type)
    asset = await service.add_asset(data=data, mime_type=mime, description=description)
    return MoodBoardMapper.to_response(asset)


@router.get("/assets", response_model=list[MoodBoardAssetResponse])
async def list_mood_board_assets(service: MoodBoardServiceDep) -> list[MoodBoardAssetResponse]:
    assets = await service.list_all()
    return [MoodBoardMapper.to_response(a) for a in assets]
