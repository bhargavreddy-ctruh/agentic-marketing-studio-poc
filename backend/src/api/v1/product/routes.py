"""HTTP only — validate, call the service, return. No business logic here (Rules.md section 2)."""
from __future__ import annotations

from fastapi import APIRouter

from ....mappers.product_mapper import ProductMapper
from ....schemas.product.requests import OnboardProductRequest
from ....schemas.product.responses import ProductProfileResponse
from ...dependencies import ProductDnaServiceDep

router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.post("", response_model=ProductProfileResponse)
async def onboard_product(
    body: OnboardProductRequest, svc: ProductDnaServiceDep
) -> ProductProfileResponse:
    product = await svc.onboard_product(
        name=body.name,
        description=body.description,
        price=body.price,
        discount_percent=body.discount_percent,
    )
    return ProductMapper.to_response(product)


@router.get("/{product_id}", response_model=ProductProfileResponse)
async def get_product(product_id: str, svc: ProductDnaServiceDep) -> ProductProfileResponse:
    product = await svc.get_product(product_id)
    return ProductMapper.to_response(product)
