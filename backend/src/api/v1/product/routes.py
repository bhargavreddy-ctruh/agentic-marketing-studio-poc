"""HTTP only — validate, call the service, return. No business logic here (Rules.md section 2)."""
from __future__ import annotations

from fastapi import APIRouter

from ....mappers.product_mapper import ProductMapper
from ....schemas.product.requests import OnboardProductRequest
from ....schemas.product.responses import ProductProfileResponse
from ...dependencies import CurrentUserDep, ProductDnaServiceDep

router = APIRouter(prefix="/api/v1/products", tags=["products"])


@router.post("", response_model=ProductProfileResponse)
async def onboard_product(
    body: OnboardProductRequest, svc: ProductDnaServiceDep, current_user: CurrentUserDep
) -> ProductProfileResponse:
    # Real, live-found bug (2026-09-25): this call used to omit `user_id` entirely even though
    # `ProductDnaService.onboard_product` requires it — a real TypeError on every real call,
    # confirmed live (the only product row ever created in this app's DB has `user_id = NULL`,
    # meaning it predates this route requiring the field). Mirrors `brand/routes.py`'s own pattern.
    product = await svc.onboard_product(
        user_id=current_user.id,
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


from fastapi import UploadFile
from ....core.local_storage import save_asset
from ....core.mime_sniff import sniff_image_mime
from ....repositories.base import ProductRepository
from ...dependencies import ProductRepositoryDep

@router.post("/{product_id}/photo")
async def upload_product_photo(
    product_id: str,
    file: UploadFile,
    svc: ProductDnaServiceDep,
    repo: ProductRepositoryDep,
):
    product = await svc.get_product(product_id)
    data = await file.read()
    mime = sniff_image_mime(data, file.content_type)
    storage_ref = save_asset(data, mime, metadata={"product_id": product_id, "type": "product_photo"})
    
    product.photo_storage_ref = storage_ref
    await repo.add(product)
    return {"product_id": product_id, "photo_storage_ref": storage_ref}
