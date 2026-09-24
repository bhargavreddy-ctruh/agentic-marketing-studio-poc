"""
Guardrail Service — Integrates the deterministic Guardrails engine with LlamaIndex and Session persistence.
"""
from __future__ import annotations

from typing import Any

from llama_index.core import Document
from ...core.guardrails import GuardrailRule, GuardrailSet, derive_guardrails, coerce_set, resolve
from ...models.session import SessionModel
from ...repositories.base import SessionRepository
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...core.middleware.logging import get_logger
from ...repositories.sqlite.sqlite_brand_repository import SqliteBrandRepository
from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from ...models.base import async_session_factory

log = get_logger(__name__)

class GuardrailService:
    def __init__(self, sessions: SessionRepository):
        self._sessions = sessions

    @staticmethod
    async def _load_brand_and_product_json(session: SessionModel) -> tuple[dict, dict]:
        """The one real place `session.brand_profile_id`/`product_profile_id` actually get
        resolved into the JSON shapes `derive_guardrails` expects — shared by
        `get_or_derive_for_session` and `link_profiles` so there's exactly one real fetch path."""
        brand_json: dict = {}
        product_json: dict = {}
        async with async_session_factory() as db:
            if session.brand_profile_id:
                brand_repo = SqliteBrandRepository(db)
                brand = await brand_repo.get(session.brand_profile_id)
                if brand:
                    brand_json = brand.raw_profile

            if session.product_profile_id:
                product_repo = SqliteProductRepository(db)
                product = await product_repo.get(session.product_profile_id)
                if product:
                    # Real, live-found bug (2026-09-24): `product.attributes` alone never has the
                    # product's own `name` — that's a sibling field on `ProductProfileModel`, not
                    # inside `attributes` — so `_product_rules`' "identity" rule (Name/Category/
                    # Description) always fired with the name genuinely missing. This app's real
                    # `ProductProfile` has no separate category/description fields to add
                    # (`attributes.summary` already covers description-shaped content via the
                    # existing dynamic attribute loop), so `name` is the one real gap to close.
                    product_json = {**product.attributes, "name": product.name}
        return brand_json, product_json

    async def get_or_derive_for_session(self, session_id: str) -> GuardrailSet:
        """
        Gets the active guardrails for a session. If none exist, derives them from the session's
        attached Brand and Product profiles.
        """
        session = await self._sessions.get(session_id)
        if not session:
            return GuardrailSet()

        existing = coerce_set(session.brief.get("guardrails"))
        if existing and existing.rules:
            return existing

        brand_json, product_json = await self._load_brand_and_product_json(session)
        new_set = derive_guardrails(brand=brand_json, product=product_json)

        # Persist and index immediately.
        session.brief = {**session.brief, "guardrails": new_set.model_dump()}
        await self._sessions.update(session)
        self.index_guardrails(session_id, new_set)

        return new_set

    async def link_profiles(
        self,
        session_id: str,
        *,
        brand_profile_id: str | None = None,
        product_profile_id: str | None = None,
    ) -> GuardrailSet:
        """Real, live-found gap (2026-09-24, per an explicit user report: "it created elements but
        it didn't generate session/product guardrails"): `SessionModel.brand_profile_id`/
        `product_profile_id` are real columns nothing anywhere ever wrote to — there was no way to
        attach a Brand/Product DNA profile to a session AT ALL, so `derive_guardrails` always ran
        with empty inputs, for every session, always. This is the one place that actually sets
        them. A `None` argument leaves that field UNCHANGED (not cleared) — this is "link/change
        one or both", not "replace the whole linkage every call".

        Re-derives guardrails from the newly-linked profile(s) and MERGES them into whatever the
        session already has (`GuardrailSet.merge` — existing ids win), never overwriting real human
        edits or previously-added custom rules just because a profile got linked."""
        session = await self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if brand_profile_id is not None:
            session.brand_profile_id = brand_profile_id or None
        if product_profile_id is not None:
            session.product_profile_id = product_profile_id or None

        brand_json, product_json = await self._load_brand_and_product_json(session)
        derived = derive_guardrails(brand=brand_json, product=product_json)

        existing = coerce_set(session.brief.get("guardrails")) or GuardrailSet()
        # Remove old derived rules (brand/product/project) so they are cleanly replaced
        human_rules = [r for r in existing.rules if r.source not in ("brand", "product", "project")]
        existing.rules = human_rules
        
        merged = existing.merge(derived.rules)

        session.brief = {**session.brief, "guardrails": merged.model_dump()}
        await self._sessions.update(session)
        self.index_guardrails(session_id, merged)

        return merged

    async def update_guardrails(self, session_id: str, new_rules_dict: dict) -> GuardrailSet:
        """
        Allows the frontend/user to manually update the active guardrails for this session.
        """
        session = await self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        updated_set = coerce_set(new_rules_dict)
        if not updated_set:
            updated_set = GuardrailSet()

        session.brief = {**session.brief, "guardrails": updated_set.model_dump()}
        await self._sessions.update(session)
        self.index_guardrails(session_id, updated_set)
        
        return updated_set
    async def add_rule_from_user_context(self, session_id: str, raw_text: str, scope: str, source: str) -> GuardrailSet:
        """
        Enhances a short user instruction into a robust guardrail using an LLM,
        then adds it to the session's active GuardrailSet.
        """
        from ...providers.llm.router import get_llm_provider
        from ...providers.llm.base import ModelTier
        from ...core.json_extract import extract_json
        import uuid
        
        prompt = f"""You are an expert marketing guardrails engineer.
The user wants to add a new custom guardrail, but they only provided a short or vague instruction.
Your job is to rewrite this into a strict, clear, and robust guardrail rule.

Original request: "{raw_text}"
Scope: "{scope}"

Write a single, robust instruction (1-3 sentences) that strictly dictates how the AI should behave.
Be specific, unambiguous, and use imperative language (e.g., "NEVER use...", "ALWAYS ensure...").

Return ONLY a JSON object with one key "rule" containing your rewritten text.
"""
        try:
            result = await get_llm_provider().complete(
                tier=ModelTier.TIER_1,
                system=prompt,
                messages=[],
                max_tokens=300
            )
            parsed = extract_json(result.text)
            enhanced_text = parsed.get("rule", raw_text)
        except Exception as e:
            log.warning("enhance_rule_failed", extra={"_extra_error": str(e)})
            enhanced_text = raw_text

        session = await self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
            
        guardrail_set = coerce_set(session.brief.get("guardrails"))
        if not guardrail_set:
            guardrail_set = GuardrailSet()
            
        new_rule = GuardrailRule(
            id=f"rule_{uuid.uuid4().hex[:8]}",
            source=source,
            rule=enhanced_text,
            scope=scope
        )
        guardrail_set.rules.append(new_rule)
        
        session.brief = {**session.brief, "guardrails": guardrail_set.model_dump()}
        await self._sessions.update(session)
        self.index_guardrails(session_id, guardrail_set)
        
        return guardrail_set
    def index_guardrails(self, session_id: str, guardrail_set: GuardrailSet) -> None:
        """
        Ingests the session's strict guardrails into a dedicated LlamaIndex collection 
        so agents can semantically search rules if needed.
        """
        try:
            provider = get_knowledge_provider()
            collection_name = f"guardrails_{session_id}"
            
            docs = []
            for r in guardrail_set.rules:
                meta = {"id": r.id, "source": r.source, "scope": r.scope}
                docs.append(Document(text=r.rule, metadata=meta, doc_id=r.id))
                
            # Fire and forget into LlamaIndex
            if docs:
                provider.index_documents(collection_name, docs)
                log.info(f"Indexed {len(docs)} guardrails for session {session_id}")
        except Exception as e:
            log.warning(f"Failed to index guardrails for session {session_id}", extra={"_extra_error": str(e)})
