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

        # We need to derive them.
        brand_json = {}
        product_json = {}

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
                    product_json = product.attributes

        new_set = derive_guardrails(brand=brand_json, product=product_json)
        
        # Persist and index immediately.
        session.brief = {**session.brief, "guardrails": new_set.model_dump()}
        await self._sessions.update(session)
        self.index_guardrails(session_id, new_set)
        
        return new_set

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
    async def add_enhanced_rule(self, session_id: str, raw_text: str, scope: str, source: str) -> GuardrailSet:
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

    async def infer_initial_guardrails(self, session_id: str, user_message: str) -> GuardrailSet:
        """
        Called on the first turn of a session to automatically infer a set of robust
        guardrails directly from the user's initial prompt.
        """
        from ...providers.llm.router import get_llm_provider
        from ...providers.llm.base import ModelTier
        from ...core.json_extract import extract_json
        import uuid
        
        prompt = f"""You are an expert brand compliance guardian. 
Your job is to read the user's initial campaign request and extract an exhaustive set of strict, exclusionary rules (guardrails) to prevent AI hallucinations.
The user has just started a new session with the following request:
"{user_message}"

Based strictly on this request, generate an exhaustive list of rules using this exact reasoning framework:
1. **Identity & Attributes**: For every specific detail mentioned (e.g., subject, setting, aesthetic), explicitly forbid contradicting it (e.g., "The campaign is about X. Imagery must not depict anything inconsistent with this").
2. **Options & Exclusivity**: If a specific list of options is given (e.g., colors, sizes, themes), strictly bound them (e.g., "The only colors allowed are red and blue. Never depict, state, or imply any color outside this list").
3. **Pricing & Figures**: If a price or discount is mentioned, lock it down exactly (e.g., "When price appears it must read exactly $X. Never a different figure, and never rounded or approximated").
4. **Claims & Hallucinations**: ALWAYS include a catch-all rule forbidding the invention of unstated facts (e.g., "Never state a price, discount, specification, or fact that was not explicitly provided. If something is absent, leave it out rather than inventing a plausible value").

Do NOT limit yourself to 2-3 rules. Generate as many rules as necessary to fully lock down every constraint implied by the request.
Be specific, unambiguous, and use imperative exclusionary language ("Never use...", "Always ensure...").

Return ONLY a JSON object containing a list of strings under the key "rules".
Example:
{{
  "rules": [
    "The only colors allowed are red and blue. Never depict, state, or imply any color outside this list.",
    "When price appears it must read exactly $199. Never a different figure, and never rounded or approximated.",
    "Never state a price, discount, specification, or fact that was not explicitly provided. If something is absent, leave it out rather than inventing a plausible value."
  ]
}}
"""
        try:
            result = await get_llm_provider().complete(
                tier=ModelTier.TIER_1,
                system=prompt,
                messages=[],
                max_tokens=800
            )
            parsed = extract_json(result.text)
            rules_text = parsed.get("rules", [])
        except Exception as e:
            log.warning("infer_initial_guardrails_failed", extra={"_extra_error": str(e)})
            rules_text = []

        session = await self._sessions.get(session_id)
        if not session:
            return GuardrailSet()
            
        guardrail_set = coerce_set(session.brief.get("guardrails"))
        if not guardrail_set:
            guardrail_set = GuardrailSet()
            
        for text in rules_text:
            if not isinstance(text, str):
                continue
            new_rule = GuardrailRule(
                id=f"rule_{uuid.uuid4().hex[:8]}",
                source="custom",
                rule=text,
                scope="all"
            )
            guardrail_set.rules.append(new_rule)
        
        if rules_text:
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
