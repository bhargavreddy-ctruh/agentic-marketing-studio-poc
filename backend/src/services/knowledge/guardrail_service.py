"""
Guardrail Service — Integrates the deterministic Guardrails engine with LlamaIndex and Session persistence.
"""
from __future__ import annotations

from ...core.guardrails import GuardrailRule, GuardrailSet, coerce_set, derive_guardrails
from ...core.middleware.logging import get_logger
from ...models.base import async_session_factory
from ...models.session import SessionModel
from ...providers.knowledge.llamaindex_provider import get_knowledge_provider
from ...repositories.base import SessionRepository
from ...repositories.postgres.postgres_brand_repository import PostgresBrandRepository
from ...repositories.postgres.postgres_product_repository import PostgresProductRepository

log = get_logger(__name__)

class GuardrailService:
    def __init__(self, sessions: SessionRepository):
        self._sessions = sessions

    @staticmethod
    async def _load_brand_and_products_json(session: SessionModel) -> tuple[dict, list[dict]]:
        """The one real place brand/product profile data is resolved into the JSON shapes
        `derive_guardrails` expects.

        Brand resolution (2026-09-25 fix): if `session.brand_profile_id` is explicitly set, use
        that. Otherwise fall back to the FIRST brand the session's user has onboarded — the common
        case for users who only have one brand (e.g. Bewakoof) and never explicitly linked it. Brand
        DNA is the same across all of a user's sessions unless they explicitly change it — there is
        deliberately no "many brands per session" concept, unlike products below.

        Product resolution (2026-09-25, real requirement: "there might be many products in one
        session" — a session's chat can describe several distinct products over its lifetime, each
        getting its own real, evolving Product DNA row, not just the single most-recent one):
        `session.brief["product_profile_ids"]` is the real list of every product this session has
        ever been linked to (appended to by `SessionService`'s chat-driven upsert and by
        `link_profiles` below). Falls back to the single `session.product_profile_id` column (older
        sessions linked before this list existed).

        Real, live-found bug (2026-09-30): this used to ALSO fall back further, to the user's
        most-recently-onboarded product ACROSS EVERY SESSION, as a bootstrap for a session that
        had never mentioned a product at all — unlike the brand fallback above (a deliberate,
        documented "one brand per user" design), a user testing multiple distinct products across
        different sessions had a brand-new, genuinely empty session silently inherit an unrelated
        product from a different session ("could you confirm you want the Nothing Phone (2a)..."
        in a session that was never about a phone at all). Removed — a session with no product
        ever linked now correctly resolves to no product configured, same as it already correctly
        shows in the Guardrails/DNA UI for that case."""
        brand_json: dict = {}
        products_json: list[dict] = []
        async with async_session_factory() as db:
            brand_repo = PostgresBrandRepository(db)
            product_repo = PostgresProductRepository(db)

            # Brand: explicit link wins, else first user brand
            brand = None
            if session.brand_profile_id:
                brand = await brand_repo.get(session.brand_profile_id)
            elif session.user_id:
                user_brands = await brand_repo.list_for_user(session.user_id)
                if user_brands:
                    brand = user_brands[0]
            if brand:
                brand_json = brand.raw_profile

            # Products: every id this session has ever been linked to, else the legacy single
            # column, else a one-product bootstrap from the user's own most-recently-onboarded one.
            product_ids = list(session.brief.get("product_profile_ids") or [])
            if not product_ids and session.product_profile_id:
                product_ids = [session.product_profile_id]
            if not product_ids and session.user_id:
                user_prods = await product_repo.list_for_user(session.user_id)
                if user_prods:
                    product_ids = [user_prods[0].id]

            products: list = []
            for pid in product_ids:
                product = await product_repo.get(pid)
                if product:
                    products.append(product)

            for product in products:
                # Real, live-found bug (2026-09-24): `product.attributes` alone never has the
                # product's own `name`/`id` — `name` is a sibling field, `id` isn't in `attributes`
                # at all — both needed here (`id` namespaces this product's rules, see
                # `core/guardrails.py::_product_rules`'s `key` param).
                products_json.append({**product.attributes, "name": product.name, "id": product.id})
        return brand_json, products_json

    async def get_brand_logo_storage_ref(self, session: SessionModel) -> str | None:
        """Same explicit-link-else-first-user-brand resolution as `_load_brand_and_products_json`
        above, exposing the one additional field guardrail derivation itself doesn't need — the
        brand's own real uploaded logo asset. Real, live-found bug (2026-09-30): a collab campaign
        image rendered literal placeholder text ("NOTHING LOGO") instead of the real logo, because
        nothing anywhere ever surfaced `BrandProfileModel.logo_storage_ref` to a specialist — only
        prose brand facts (colors/voice) reached them via `brand_kit_lookup`."""
        async with async_session_factory() as db:
            brand_repo = PostgresBrandRepository(db)
            brand = None
            if session.brand_profile_id:
                brand = await brand_repo.get(session.brand_profile_id)
            elif session.user_id:
                user_brands = await brand_repo.list_for_user(session.user_id)
                if user_brands:
                    brand = user_brands[0]
            return brand.logo_storage_ref if brand else None

    async def get_or_derive_for_session(self, session_id: str) -> GuardrailSet:
        """Gets guardrails for a session, always ensuring brand and product rules are up-to-date.

        Real, live-found gap (2026-09-25): the old implementation returned early as soon as ANY
        rules existed — this meant a session created without an explicit brand link would NEVER get
        brand guardrails even if the user had a brand onboarded, because the first call only derived
        from empty inputs and the early-return blocked every subsequent call from retrying.

        New behavior:
        - Human-added rules (source='human') AND freeform chat-stated constraints with nowhere else
          to live (source='custom' — e.g. "must be red", "winter campaign"; see
          `SessionService`'s `new_guardrails` handling) are ALWAYS preserved as-is. Real, live-found
          bug fixed here (2026-09-25): the previous version only protected `source == "human"`, so
          every 'custom' rule a user's chat message ever produced was silently wiped the very next
          time this ran, since it isn't derivable from any brand/product profile and would never
          come back once dropped.
        - Brand and product rules are ALWAYS re-derived fresh from the user's real profiles and
          REPLACED — so if a user updates their brand DNA or a product's DNA changes, the next page
          load automatically reflects the new rules without any extra action."""
        session = await self._sessions.get(session_id)
        if not session:
            return GuardrailSet()

        existing = coerce_set(session.brief.get("guardrails")) or GuardrailSet()
        # Preserve human-added and freeform-custom rules — neither is derivable from a profile.
        human_rules = [r for r in existing.rules if r.source in ("human", "custom")]

        brand_json, products_json = await self._load_brand_and_products_json(session)
        derived = derive_guardrails(brand=brand_json, products=products_json)

        # Human rules take precedence; derived rules fill in what's missing.
        base = GuardrailSet(rules=human_rules)
        new_set = base.merge(derived.rules)

        # Only write to DB if the rule set actually changed.
        if new_set.model_dump() != existing.model_dump():
            session.brief = {**session.brief, "guardrails": new_set.model_dump()}
            await self._sessions.update(session)
            await self.index_guardrails(session_id, new_set)

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
        edits or previously-added custom rules just because a profile got linked.

        An explicitly-linked product (2026-09-25) joins `session.brief["product_profile_ids"]` — the
        same real multi-product list the chat-driven path appends to (`SessionService`'s
        `new_guardrails` handling) — rather than replacing it, since a session can legitimately be
        tracking several products (see `_load_brand_and_products_json`)."""
        session = await self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if brand_profile_id is not None:
            session.brand_profile_id = brand_profile_id or None
        if product_profile_id is not None:
            session.product_profile_id = product_profile_id or None
            product_ids = list(session.brief.get("product_profile_ids") or [])
            if product_profile_id and product_profile_id not in product_ids:
                product_ids.append(product_profile_id)
                session.brief = {**session.brief, "product_profile_ids": product_ids}

        brand_json, products_json = await self._load_brand_and_products_json(session)
        derived = derive_guardrails(brand=brand_json, products=products_json)

        existing = coerce_set(session.brief.get("guardrails")) or GuardrailSet()
        # Remove old derived rules (brand/product/project) so they are cleanly replaced
        human_rules = [r for r in existing.rules if r.source not in ("brand", "product", "project")]
        existing.rules = human_rules

        merged = existing.merge(derived.rules)

        session.brief = {**session.brief, "guardrails": merged.model_dump()}
        await self._sessions.update(session)
        await self.index_guardrails(session_id, merged)

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
        await self.index_guardrails(session_id, updated_set)
        
        return updated_set
    async def add_rule_from_user_context(self, session_id: str, raw_text: str, scope: str, source: str) -> GuardrailSet:
        """
        Enhances a short user instruction into a robust guardrail using an LLM,
        then adds it to the session's active GuardrailSet.
        """
        import uuid

        from ...core.json_extract import extract_json
        from ...providers.llm.base import ModelTier
        from ...providers.llm.router import get_llm_provider
        
        prompt = f"""You are an expert marketing guardrails engineer.
The user wants to add custom guardrails based on this input:
"{raw_text}"
Scope: "{scope}"

Break down this input into one or more strict, clear, and robust guardrail rules (1-2 sentences each).
Be specific, unambiguous, and use imperative language (e.g., "NEVER use...", "ALWAYS ensure...").

Return ONLY a JSON object with one key "rules" containing a list of strings, each being a rewritten rule.
"""
        try:
            result = await get_llm_provider().complete(
                tier=ModelTier.TIER_1,
                system=prompt,
                messages=[],
                max_tokens=500
            )
            parsed = extract_json(result.text)
            enhanced_texts = parsed.get("rules", [raw_text])
            if not isinstance(enhanced_texts, list):
                enhanced_texts = [raw_text]
        except Exception as e:
            log.warning("enhance_rule_failed", extra={"_extra_error": str(e)})
            enhanced_texts = [raw_text]

        session = await self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
            
        guardrail_set = coerce_set(session.brief.get("guardrails"))
        if not guardrail_set:
            guardrail_set = GuardrailSet()
            
        for text in enhanced_texts:
            new_rule = GuardrailRule(
                id=f"rule_{uuid.uuid4().hex[:8]}",
                source=source,
                rule=text,
                scope=scope
            )
            guardrail_set.rules.append(new_rule)
        
        session.brief = {**session.brief, "guardrails": guardrail_set.model_dump()}
        await self._sessions.update(session)
        await self.index_guardrails(session_id, guardrail_set)
        
        return guardrail_set
    async def index_guardrails(self, session_id: str, guardrail_set: GuardrailSet) -> None:
        """
        Ingests the session's strict guardrails into a dedicated LlamaIndex collection
        so agents can semantically search rules if needed.

        Real, live-found bug (2026-09-25, caught while restarting the backend for unrelated work):
        this called `provider.index_documents(...)` (plural, batch) — a method that has never
        existed on `LlamaIndexKnowledgeProvider` (only singular `index_document` does). Every call
        here has been silently failing since it was written, caught only by this method's own
        try/except — guardrails were never actually reaching this collection. Fixed by calling the
        real, existing per-document method once per rule, and made properly `async` so it's a real
        awaited call, not a fire-and-forget that was actually just discarding a coroutine object
        (`provider.index_documents(...)` used to be a plain sync call anyway, which never returned
        a coroutine to discard — it just raised `AttributeError` immediately, caught right there).
        """
        try:
            provider = get_knowledge_provider()
            collection_name = f"guardrails_{session_id}"
            for r in guardrail_set.rules:
                await provider.index_document(collection=collection_name, doc_id=r.id, text=r.rule)
            if guardrail_set.rules:
                log.info(f"Indexed {len(guardrail_set.rules)} guardrails for session {session_id}")
        except Exception as e:
            log.warning(f"Failed to index guardrails for session {session_id}", extra={"_extra_error": str(e)})
