"""Entity <-> DTO for brand profiles. Rules.md: never return an entity/model object from a service."""
from __future__ import annotations

from ..core.local_storage import public_url
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
            logo_storage_ref=entity.logo_storage_ref,
            font_storage_refs=entity.font_storage_refs or {},
            logo_url=public_url(entity.logo_storage_ref),
        )
