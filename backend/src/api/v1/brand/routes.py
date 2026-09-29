"""HTTP only — validate, call the service, return. No business logic here (Rules.md section 2)."""
from __future__ import annotations

from fastapi import APIRouter

from ....mappers.brand_mapper import BrandMapper
from ....schemas.brand.requests import OnboardBrandRequest
from ....schemas.brand.responses import BrandProfileResponse
from ...dependencies import BrandDnaServiceDep, CurrentUserDep

router = APIRouter(prefix="/api/v1/brands", tags=["brands"])


@router.post("", response_model=BrandProfileResponse)
async def onboard_brand(
    body: OnboardBrandRequest, svc: BrandDnaServiceDep, current_user: CurrentUserDep
) -> BrandProfileResponse:
    """Serves both "create a new brand" (no `brand_id`) and, since 2026-09-25 ("give the user
    option to edit" their brand's real facts), "replace this existing brand's facts" (`brand_id`
    given) — ownership-checked via `get_brand` (raises `Forbidden` on a mismatch) before any write,
    same as every other brand route here already does."""
    if body.brand_id:
        await svc.get_brand(body.brand_id, user_id=current_user.id)
    brand = await svc.onboard_brand(
        user_id=current_user.id, name=body.name, raw_facts=body.raw_facts,
        brand_id=body.brand_id, merge=False,
    )
    return await BrandMapper.to_response(brand)


@router.get("", response_model=list[BrandProfileResponse])
async def list_brands(
    svc: BrandDnaServiceDep, current_user: CurrentUserDep
) -> list[BrandProfileResponse]:
    """The current user's own brand DNA profiles (Tasks_Workflows.md #3) — the home page's
    Brand DNA settings panel real data source."""
    brands = await svc.list_brands(user_id=current_user.id)
    return [await BrandMapper.to_response(b) for b in brands]


@router.get("/{brand_id}", response_model=BrandProfileResponse)
async def get_brand(
    brand_id: str, svc: BrandDnaServiceDep, current_user: CurrentUserDep
) -> BrandProfileResponse:
    brand = await svc.get_brand(brand_id, user_id=current_user.id)
    return await BrandMapper.to_response(brand)


from fastapi import UploadFile

from ....core.local_storage import save_asset
from ....core.mime_sniff import sniff_image_mime
from ...dependencies import BrandRepositoryDep


@router.post("/{brand_id}/logo")
async def upload_brand_logo(
    brand_id: str,
    file: UploadFile,
    svc: BrandDnaServiceDep,
    repo: BrandRepositoryDep,
    current_user: CurrentUserDep,
):
    brand = await svc.get_brand(brand_id, user_id=current_user.id)
    data = await file.read()
    mime = sniff_image_mime(data, file.content_type)
    storage_ref = await save_asset(data, mime, metadata={"brand_id": brand_id, "type": "brand_logo"})
    
    brand.logo_storage_ref = storage_ref
    await repo.add(brand)
    return {"brand_id": brand_id, "logo_storage_ref": storage_ref}


@router.post("/{brand_id}/fonts")
async def upload_brand_font(
    brand_id: str,
    file: UploadFile,
    svc: BrandDnaServiceDep,
    repo: BrandRepositoryDep,
    current_user: CurrentUserDep,
    font_family: str = "primary",
):
    brand = await svc.get_brand(brand_id, user_id=current_user.id)
    data = await file.read()
    mime = file.content_type or "font/ttf"
    storage_ref = await save_asset(data, mime, metadata={"brand_id": brand_id, "font_family": font_family})
    
    font_refs = dict(brand.font_storage_refs or {})
    font_refs[font_family] = storage_ref
    brand.font_storage_refs = font_refs
    await repo.add(brand)
    return {"brand_id": brand_id, "font_storage_refs": font_refs}
