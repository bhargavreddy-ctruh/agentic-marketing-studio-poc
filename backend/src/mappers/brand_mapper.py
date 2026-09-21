"""Entity <-> DTO for brand profiles. Rules.md: never return an entity/model object from a service."""
from __future__ import annotations

from ..models.brand_profile import BrandProfileModel
from ..schemas.brand.responses import BrandProfileResponse


class BrandMapper:
    @staticmethod
    def to_response(entity: BrandProfileModel) -> BrandProfileResponse:
        return BrandProfileResponse(
            id=entity.id,
            name=entity.name,
            raw_facts=entity.raw_profile.get("raw_facts", {}),
            guardrails=entity.raw_profile.get("guardrails", {}),
            indexed=entity.indexed,
            created_at=entity.created_at,
        )
