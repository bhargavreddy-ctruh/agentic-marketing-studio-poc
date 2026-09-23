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

# Brand and project fields that become one rule each, with the imperative used to state them.
_BRAND_RULE_TEMPLATES: dict[str, str] = {
    "voice_and_tone": "All copy must match this voice and tone: {value}",
    "visual_identity": "All imagery must match this visual identity: {value}",
    "positioning_and_pillars": "Messaging must support this positioning: {value}",
    "audience_segments": "Work must address this audience: {value}",
    "category_context": "Work must fit this category context: {value}",
    "logo_rules": "Logo usage must obey: {value}",
}

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

    # All other arbitrary attributes in the JSON mapping
    for key, value in product.items():
        if key in ("name", "category", "description", "price", "discount"):
            continue
        # Only parse scalar fields as rigid rules, lists as options
        if isinstance(value, str) or isinstance(value, int) or isinstance(value, float):
            add(f"attr.{_slug(key)}",
                f"{key} is {value}. Copy must not contradict that, and imagery must not "
                f"depict something inconsistent with it.")
        elif isinstance(value, list) and all(isinstance(x, str) for x in value):
            add(f"option.{_slug(key)}",
                f"The only {key} values that exist are: {', '.join(value)}. Never state, "
                f"imply or depict a {key} outside that list, and never claim a range the "
                "list does not cover.")

    add("claims",
        "Never state a price, discount, specification, availability, guarantee or "
        "any other fact about the product that the details above do not give you. "
        "If something is absent, leave it out rather than inventing a plausible "
        "value — an invented fact reads exactly like a real one.")

    return rules


def derive_guardrails(brand: dict = None, product: dict = None, project: dict = None) -> GuardrailSet:
    """
    The rules that follow directly from the JSON fields supplied.
    Deterministic on purpose: the same inputs yield the same rules.
    """
    rules: list[GuardrailRule] = []

    brand = brand or {}
    for field, template in _BRAND_RULE_TEMPLATES.items():
        value = _clean(brand.get(field))
        if value:
            rules.append(
                GuardrailRule(
                    id=f"brand.{field}",
                    rule=template.format(value=value),
                    source=SOURCE_BRAND,
                    scope=SCOPE_CAMPAIGN,
                )
            )

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
