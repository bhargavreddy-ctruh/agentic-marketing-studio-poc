"""
Guardrail Synthesizer — Architecture.md section 2.2, section 6: even in trimmed form, synthesizes
the visual-only subset of guardrails (approved colors, logo rules, prohibited imagery, price-overlay
accuracy) from raw Brand DNA facts. The messaging/claims/audience-appropriateness categories the
existing agentic_flow codebase's `run_guardrail_agent` also produces stay dormant here — this POC
has no Copy Lead, so there's no copy to guard yet (Rules.md section 6, PRD.md's explicit exclusion).

A near-port of `run_guardrail_agent`'s shape (rule objects with id/rule/severity), trimmed to only
the categories this POC's specialists can actually act on.
"""
from __future__ import annotations

from ...core.exceptions import SpecialistFailed
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable

log = get_logger(__name__)

_SYSTEM_PROMPT = """You are the Guardrail Synthesizer for a brand's visual marketing assets.

Given raw brand facts, produce a trimmed, visual-only guardrail set: approved colors, logo usage
rules, prohibited imagery, and a note on price/discount overlay accuracy requirements. Do not
invent facts that weren't given — if a category has no real basis in the input, return an empty
list for it rather than fabricating a plausible-sounding rule.

Scope every "approved colors" rule you write to brand-OWNED visual elements — backgrounds,
accents, brand graphics, packaging/logo treatments — never to the literal, real-world color of a
product being photographed or depicted. A product's own genuine physical color (e.g. a red
sneaker) is a real product fact, not a brand-identity choice, and must never be treated as a
violation of this palette. State that scope explicitly in the rule text itself.

Return ONLY JSON:
{
  "summary": "one or two sentences overviewing the guardrail set",
  "visual": [{"id": "vis-1", "rule": "...", "severity": "high"}],
  "price_overlay": [{"id": "price-1", "rule": "...", "severity": "high"}]
}
"""


@traceable(name="guardrail_synthesizer")
async def synthesize_guardrails(*, raw_facts: dict) -> dict:
    """Runs one LLM call to synthesize a trimmed guardrail set from raw brand facts. Raises
    SpecialistFailed on provider/parse failure, matching every other LLM-backed service in this
    codebase (Rules.md section 4: typed errors, not a mix of exception types)."""
    llm = get_llm_provider()
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_2,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Raw brand facts:\n{raw_facts}"}],
            max_tokens=1536,
        )
        parsed = extract_json(result.text)
    except Exception as exc:
        log.error("guardrail_synthesis_failed", extra={"_extra_error": str(exc)})
        raise SpecialistFailed("guardrail_synthesizer", str(exc)) from exc

    def _rule_list(value) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [
            r for r in value
            if isinstance(r, dict) and r.get("id") and r.get("rule") and r.get("severity")
        ]

    return {
        "summary": str(parsed.get("summary") or ""),
        "visual": _rule_list(parsed.get("visual")),
        "price_overlay": _rule_list(parsed.get("price_overlay")),
    }
