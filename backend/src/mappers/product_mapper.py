"""Entity <-> DTO for product profiles. Rules.md: never return an entity/model object from a service."""
from __future__ import annotations

from ..models.product_profile import ProductProfileModel
from ..schemas.product.responses import ProductProfileResponse


class ProductMapper:
    @staticmethod
    def to_response(entity: ProductProfileModel) -> ProductProfileResponse:
        return ProductProfileResponse(
            id=entity.id,
            name=entity.name,
            attributes=entity.attributes,
            indexed=entity.indexed,
            created_at=entity.created_at,
        )
