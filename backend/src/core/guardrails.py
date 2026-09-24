"""
guardrails.py — The deterministic rule set the Guardian/Orchestrator judges against.

Adapted from reference codebase. Transforms loose JSON strings from Brand/Product
profiles into concrete, traceable, round-trippable rules with discrete IDs.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

# Where a rule came from, in descending authority.
SOURCE_BRAND = "brand"
SOURCE_PROJECT = "project"
SOURCE_PRODUCT = "product"
SOURCE_INFERRED = "inferred"
SOURCE_HUMAN = "human"

ALL_SOURCES = (SOURCE_BRAND, SOURCE_PROJECT, SOURCE_PRODUCT, SOURCE_INFERRED, SOURCE_HUMAN)

# How widely a rule applies.
SCOPE_CAMPAIGN = "campaign"
SCOPE_SEGMENT = "segment"
SCOPE_ASSET_TYPE = "asset_type"

ALL_SCOPES = (SCOPE_CAMPAIGN, SCOPE_SEGMENT, SCOPE_ASSET_TYPE)

GUARDRAIL_VERSION = 1

# Real, live-found bug (2026-09-24, per an explicit user report: "it created elements but it
# didn't generate session/product guardrails"): `_BRAND_RULE_TEMPLATES` below was dead on arrival
# for every real brand ever onboarded through this app. Two compounding mismatches, confirmed by
# reading the real data both sides actually produce/consume: (1) `BrandProfileModel.raw_profile` is
# stored as `{"raw_facts": {...}, "guardrails": {...}}` (`brand_dna_service.py`), but this used to
# be handed the raw_facts VALUE directly with no unwrapping — every `.get(field)` below read a key
# that was never at that level. (2) Even unwrapped, `raw_facts` is deliberately FREE-FORM (see
# `lib/brand.ts`'s own comment: "the backend's own LLM-based guardrail synthesis is what extracts
# real structure from it, not this client") — the real onboarding form only ever saves `colors`/
# `voice`/`prohibited_imagery`, never any of the 6 fixed keys `_BRAND_RULE_TEMPLATES` looked for.
# The REAL, already-correct source of structured brand guardrails is the LLM synthesis that
# already runs at onboarding time (`guardrail_synthesizer.py`'s `visual`/`price_overlay` rule
# lists, stored right there in `raw_profile["guardrails"]`) — `_rules_from_synthesized_brand`
# below consumes that directly instead of re-guessing a second, rigid schema over free-form facts.
_PROJECT_RULE_TEMPLATES: dict[str, str] = {
    "goal": "Work must serve this goal: {value}",
    "audience": "Work must address this audience: {value}",
    "campaign_description": "Work must stay within this campaign's description: {value}",
}


class GuardrailRule(BaseModel):
    id: str
    rule: str
    source: str = SOURCE_INFERRED
    scope: str = SCOPE_CAMPAIGN
    applies_to: Optional[str] = None

    class Config:
        extra = "allow"

    def needs_confirmation(self) -> bool:
        """Inferred rules were nobody's decision until someone confirms them."""
        return self.source == SOURCE_INFERRED

    def as_line(self) -> str:
        where = f" [{self.scope}: {self.applies_to}]" if self.applies_to else ""
        flag = " (INFERRED — not confirmed by a human)" if self.needs_confirmation() else ""
        return f"{self.id}{where}: {self.rule}{flag}"


class GuardrailSet(BaseModel):
    version: int = GUARDRAIL_VERSION
    rules: list[GuardrailRule] = Field(default_factory=list)

    class Config:
        extra = "allow"

    def __bool__(self) -> bool:
        return bool(self.rules)

    def ids(self) -> list[str]:
        return [r.id for r in self.rules]

    def inferred(self) -> list[GuardrailRule]:
        return [r for r in self.rules if r.needs_confirmation()]

    def get(self, rule_id: str) -> GuardrailRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def render(self) -> str:
        """The rule list as the Guardian/LLM sees it."""
        if not self.rules:
            return ""
        lines = "\n".join(f"- {r.as_line()}" for r in self.rules)
        return f"<guardrails>\n{lines}\n</guardrails>"

    def merge(self, extra: Iterable[GuardrailRule]) -> "GuardrailSet":
        """
        Add rules that are not already present. Existing rules win on id collision so human 
        amendments are never overwritten by freshly inferred rules.
        """
        known = {r.id for r in self.rules}
        merged = list(self.rules)
        for rule in extra:
            if rule.id not in known:
                merged.append(rule)
                known.add(rule.id)
        return GuardrailSet(version=self.version, rules=merged)


def _clean(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit] if text else ""


def _slug(text: str) -> str:
    """A stable id fragment from an arbitrary attribute name."""
    out = "".join(c.lower() if c.isalnum() else "_" for c in str(text).strip())
    return "_".join(p for p in out.split("_") if p)[:40] or "field"


def _product_rules(product: dict) -> list[GuardrailRule]:
    """Rules from the product JSON attributes."""
    if not product:
        return []

    rules: list[GuardrailRule] = []

    def add(suffix: str, text: str) -> None:
        rules.append(
            GuardrailRule(id=f"product.{suffix}", rule=text,
                          source=SOURCE_PRODUCT, scope=SCOPE_CAMPAIGN)
        )

    # Dynamic parsing of standard product fields
    identity = [
        f"{label}: {_clean(product.get(field))}"
        for field, label in (("name", "Name"), ("category", "Category"), ("description", "Description"))
        if _clean(product.get(field))
    ]
    if identity:
        add("identity", "Every asset must show and describe this exact product — " + "; ".join(identity))

    # Specific handling for product DNA fields
    if product.get("must_show"):
        add("must_show", f"You must always show or clearly depict: {', '.join(product['must_show'])}.")
    
    if product.get("never_show"):
        add("never_show", f"You must never show, imply, or depict: {', '.join(product['never_show'])}.")
        
    if product.get("claims_allowed"):
        add("claims_allowed", f"You are allowed to make the following claims: {', '.join(product['claims_allowed'])}.")
        
    if product.get("claims_disallowed"):
        add("claims_disallowed", f"You are strictly prohibited from making the following claims: {', '.join(product['claims_disallowed'])}.")
        
    if product.get("label_visibility"):
        add("label_visibility", f"Label visibility requirement: {product['label_visibility']}.")

    # All other arbitrary attributes in the JSON mapping
    handled_keys = {"name", "category", "description", "price", "discount", "summary", "discount_percent", "must_show", "never_show", "claims_allowed", "claims_disallowed", "label_visibility"}
    for key, value in product.items():
        if key in handled_keys:
            continue
        # Only parse scalar fields as rigid rules, lists as options
        if isinstance(value, str) or isinstance(value, int) or isinstance(value, float):
            add(f"attr.{_slug(key)}",
                f"The {key} is {value}. Copy must not contradict that, and imagery must not "
                f"depict something inconsistent with it.")
        elif isinstance(value, list) and all(isinstance(x, str) for x in value):
            add(f"option.{_slug(key)}",
                f"The only approved {key} options are: {', '.join(value)}. Never state, "
                f"imply or depict a {key} outside that list.")

    add("claims",
        "Never state a price, discount, specification, availability, guarantee or "
        "any other fact about the product that the details above do not give you. "
        "If something is absent, leave it out rather than inventing a plausible "
        "value — an invented fact reads exactly like a real one.")

    return rules


def _rules_from_synthesized_brand(brand: dict) -> list[GuardrailRule]:
    """Converts the REAL, already-LLM-synthesized brand guardrails
    (`guardrail_synthesizer.py`'s `visual`/`price_overlay` rule lists, produced once at brand
    onboarding and stored in `BrandProfileModel.raw_profile["guardrails"]`) into `GuardrailRule`
    objects — see the real bug this replaces, in the comment above `_PROJECT_RULE_TEMPLATES`."""
    synthesized = (brand or {}).get("guardrails")
    if not isinstance(synthesized, dict):
        return []
    rules: list[GuardrailRule] = []
    for category in ("visual", "price_overlay"):
        for i, item in enumerate(synthesized.get(category) or []):
            if not isinstance(item, dict):
                continue
            rule_text = _clean(item.get("rule"), limit=1200)
            if not rule_text:
                continue
            raw_id = _clean(item.get("id")) or f"{category}_{i + 1}"
            rule = GuardrailRule(
                id=f"brand.{_slug(raw_id)}",
                rule=rule_text,
                source=SOURCE_BRAND,
                scope=SCOPE_CAMPAIGN,
            )
            severity = _clean(item.get("severity"))
            if severity:
                # `Config.extra = "allow"` — a real, additional field the synthesizer produced,
                # kept rather than discarded, without forcing every OTHER rule source to carry one.
                rule.severity = severity
            rules.append(rule)
    return rules


def derive_guardrails(brand: dict = None, product: dict = None, project: dict = None) -> GuardrailSet:
    """
    The rules that follow directly from the JSON fields supplied.
    Deterministic on purpose: the same inputs yield the same rules.
    """
    rules: list[GuardrailRule] = []

    rules.extend(_rules_from_synthesized_brand(brand or {}))

    project = project or {}
    for field, template in _PROJECT_RULE_TEMPLATES.items():
        value = _clean(project.get(field))
        if value:
            rules.append(
                GuardrailRule(
                    id=f"project.{field}",
                    rule=template.format(value=value),
                    source=SOURCE_PROJECT,
                    scope=SCOPE_CAMPAIGN,
                )
            )

    rules.extend(_product_rules(product or {}))
    return GuardrailSet(version=GUARDRAIL_VERSION, rules=rules)


def coerce_rule(raw: Any, *, fallback_id: str = "") -> GuardrailRule | None:
    if isinstance(raw, GuardrailRule):
        return raw
    if not isinstance(raw, dict):
        return None
    text = _clean(raw.get("rule"), limit=1200)
    if not text:
        return None
    rule_id = _clean(raw.get("id"), limit=120) or fallback_id
    if not rule_id:
        return None
    source = _clean(raw.get("source"), limit=40) or SOURCE_INFERRED
    scope = _clean(raw.get("scope"), limit=40) or SCOPE_CAMPAIGN
    try:
        return GuardrailRule(
            id=rule_id,
            rule=text,
            source=source if source in ALL_SOURCES else SOURCE_INFERRED,
            scope=scope if scope in ALL_SCOPES else SCOPE_CAMPAIGN,
            applies_to=_clean(raw.get("applies_to"), limit=120) or None,
        )
    except Exception:
        return None


def coerce_set(raw: Any) -> GuardrailSet | None:
    """Accept a model, a dict, or nothing. Never raises."""
    if raw is None:
        return None
    if isinstance(raw, GuardrailSet):
        return raw
    if not isinstance(raw, dict):
        return None

    rules: list[GuardrailRule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("rules") or []):
        rule = coerce_rule(item, fallback_id=f"rule.{index + 1}")
        if rule is not None and rule.id not in seen:
            rules.append(rule)
            seen.add(rule.id)

    version = raw.get("version")
    return GuardrailSet(
        version=int(version) if isinstance(version, int) else GUARDRAIL_VERSION,
        rules=rules,
    )


def resolve(
    supplied: Any = None,
    *,
    brand: dict = None,
    product: dict = None,
    project: dict = None,
) -> GuardrailSet:
    """
    The set this call should use. A supplied set is authoritative and is returned as given
    — it may carry human amendments. Only when none was supplied is one derived.
    """
    existing = coerce_set(supplied)
    if existing is not None and existing.rules:
        return existing
    return derive_guardrails(brand, product, project)
