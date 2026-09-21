"""HTTP only — validate, call the service, return. No business logic here (Rules.md section 2)."""
from __future__ import annotations

from fastapi import APIRouter

from ....mappers.brand_mapper import BrandMapper
from ....schemas.brand.requests import OnboardBrandRequest
from ....schemas.brand.responses import BrandProfileResponse
from ...dependencies import BrandDnaServiceDep

router = APIRouter(prefix="/api/v1/brands", tags=["brands"])


@router.post("", response_model=BrandProfileResponse)
async def onboard_brand(body: OnboardBrandRequest, svc: BrandDnaServiceDep) -> BrandProfileResponse:
    brand = await svc.onboard_brand(name=body.name, raw_facts=body.raw_facts)
    return BrandMapper.to_response(brand)


@router.get("/{brand_id}", response_model=BrandProfileResponse)
async def get_brand(brand_id: str, svc: BrandDnaServiceDep) -> BrandProfileResponse:
    brand = await svc.get_brand(brand_id)
    return BrandMapper.to_response(brand)
