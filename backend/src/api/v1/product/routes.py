"""HTTP only — validate, call the service, return. No business logic here (Rules.md section 2)."""
from __future__ import annotations

from fastapi import APIRouter

from ....core.exceptions import Forbidden
from ....mappers.product_mapper import ProductMapper
from ....models.product_profile import ProductProfileModel
from ....models.user import UserModel
from ....schemas.product.requests import OnboardProductRequest, UpdateProductRequest
from ....schemas.product.responses import ProductProfileResponse
from ....services.knowledge.product_dna_service import ProductDnaService
from ...dependencies import CurrentUserDep, ProductDnaServiceDep

router = APIRouter(prefix="/api/v1/products", tags=["products"])


async def _owned_product(
    product_id: str, svc: ProductDnaService, current_user: UserModel
) -> ProductProfileModel:
    """Every product route below goes through this (closing a real, disclosed gap — these routes
    used to have no auth dependency, and `GET`/`PATCH`/`DELETE`/photo-upload didn't check ownership
    even on the ones that did). `svc.get_product` already raises `NotFoundError` for a missing id;
    this adds the ownership check on top, same strict rule session ownership already uses — a
    product with no `user_id` (pre-auth/orphaned data) is never treated as owned by everyone."""
    product = await svc.get_product(product_id)
    if product.user_id != current_user.id:
        raise Forbidden("This product belongs to a different user")
    return product


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
        color=body.color,
    )
    return ProductMapper.to_response(product)


@router.get("/{product_id}", response_model=ProductProfileResponse)
async def get_product(
    product_id: str, svc: ProductDnaServiceDep, current_user: CurrentUserDep
) -> ProductProfileResponse:
    product = await _owned_product(product_id, svc, current_user)
    return ProductMapper.to_response(product)


@router.patch("/{product_id}", response_model=ProductProfileResponse)
async def update_product(
    product_id: str, body: UpdateProductRequest, svc: ProductDnaServiceDep, current_user: CurrentUserDep
) -> ProductProfileResponse:
    """Edit a wrongly-crawled or outdated Product DNA profile (2026-09-28) — patch semantics, only
    the fields actually given are changed."""
    await _owned_product(product_id, svc, current_user)
    product = await svc.update_product_attributes(
        product_id, name=body.name, attributes_patch=body.attributes_patch
    )
    return ProductMapper.to_response(product)


@router.delete("/{product_id}", status_code=204)
async def delete_product(product_id: str, svc: ProductDnaServiceDep, current_user: CurrentUserDep) -> None:
    """Delete a wrongly-crawled or outdated Product DNA profile (2026-09-28). Does not cascade to
    canvas elements tagged with this product — see `ProductDnaService.delete_product`'s own
    docstring."""
    await _owned_product(product_id, svc, current_user)
    await svc.delete_product(product_id)


from fastapi import UploadFile

from ....core.local_storage import save_asset
from ....core.mime_sniff import sniff_image_mime
from ...dependencies import ProductRepositoryDep


@router.post("/{product_id}/photo")
async def upload_product_photo(
    product_id: str,
    file: UploadFile,
    svc: ProductDnaServiceDep,
    repo: ProductRepositoryDep,
    current_user: CurrentUserDep,
):
    product = await _owned_product(product_id, svc, current_user)
    data = await file.read()
    mime = sniff_image_mime(data, file.content_type)
    storage_ref = await save_asset(data, mime, metadata={"product_id": product_id, "type": "product_photo"})
    
    product.photo_storage_ref = storage_ref
    await repo.add(product)
    return {"product_id": product_id, "photo_storage_ref": storage_ref}
