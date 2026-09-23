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
    brand = await svc.onboard_brand(user_id=current_user.id, name=body.name, raw_facts=body.raw_facts)
    return BrandMapper.to_response(brand)


@router.get("", response_model=list[BrandProfileResponse])
async def list_brands(
    svc: BrandDnaServiceDep, current_user: CurrentUserDep
) -> list[BrandProfileResponse]:
    """The current user's own brand DNA profiles (Tasks_Workflows.md #3) — the home page's
    Brand DNA settings panel real data source."""
    brands = await svc.list_brands(user_id=current_user.id)
    return [BrandMapper.to_response(b) for b in brands]


@router.get("/{brand_id}", response_model=BrandProfileResponse)
async def get_brand(
    brand_id: str, svc: BrandDnaServiceDep, current_user: CurrentUserDep
) -> BrandProfileResponse:
    brand = await svc.get_brand(brand_id, user_id=current_user.id)
    return BrandMapper.to_response(brand)
