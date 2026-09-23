"""
guardrails.py — The rule set the Guardian judges against.

Brand details are prose. "Warm, plain-spoken, never salesy" is a sentence, not a
check: two agents can both believe they satisfied it, and a human who disagrees
has nothing to point at. A guardrail set turns that prose into discrete rules
with identities, so a verdict can name the rule it breached and a person can
change one rule without rewriting the brand.

Three properties do the work:

* **Stable ids.** A rule's id comes from where it came from, not its position in
  the list. Positional ids (`r1`, `r2`) look tidier and are a trap: add one brand
  field and every later id shifts, so a human's amendment silently reattaches to
  a different rule.
* **Recorded origin.** A rule quoted from a brand field and a rule the model
  inferred are not equally authoritative. `source` keeps them apart so inferred
  rules can be shown for confirmation rather than enforced as though a human
  wrote them.
* **Round-tripping.** The caller resends the set on every call. Derivation from
  the same brand is stable, so that matters less than it sounds for derived
  rules — but inferred rules and human amendments exist nowhere else. Drop the
  set and they are gone, and the campaign is quietly judged by a different
  standard than the one someone approved.
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

# Brand and project fields that become one rule each, with the imperative used to
# state them. The label matters: "Voice and tone: warm" is a description, while
# "All copy must match this voice and tone: warm" is something a verdict can test.
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
        """The rule list as the Guardian sees it."""
        if not self.rules:
            return ""
        lines = "\n".join(f"- {r.as_line()}" for r in self.rules)
        return f"<guardrails>\n{lines}\n</guardrails>"

    def scoped_to(
        self,
        *,
        segment: str | None = None,
        asset_type: str | None = None,
    ) -> "GuardrailSet":
        """
        The rules that apply to what is being made right now.

        A view, never a replacement: the full set is what gets returned to the
        caller and round-tripped. Filtering the stored set instead would quietly
        delete every other segment's rules the first time one segment ran.

        Campaign rules always apply. A scoped rule applies only to its own
        subject, which is what makes "price only on the hero, not the teaser"
        expressible — and a scoped rule with nothing to apply to is treated as
        campaign-wide, because a rule that can never match is a rule that was
        silently switched off.
        """
        kept: list[GuardrailRule] = []
        for rule in self.rules:
            if rule.scope == SCOPE_SEGMENT and rule.applies_to:
                if segment and rule.applies_to.strip().lower() == segment.strip().lower():
                    kept.append(rule)
                continue
            if rule.scope == SCOPE_ASSET_TYPE and rule.applies_to:
                if asset_type and rule.applies_to.strip().lower() == asset_type.strip().lower():
                    kept.append(rule)
                continue
            kept.append(rule)
        return GuardrailSet(version=self.version, rules=kept)

    def merge(self, extra: Iterable[GuardrailRule]) -> "GuardrailSet":
        """
        Add rules that are not already present.

        Existing rules win on id collision: a rule already in the set may carry a
        human's amendment, and a freshly inferred rule must never overwrite that.
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


def _product_rules(product: Any) -> list[GuardrailRule]:
    """
    Rules from what the catalogue states, as opposed to what a photograph shows.

    These are the ones a caption gets wrong. A model writing "50% OFF" or "slim
    fit" is not being careless about pixels, it is asserting a fact nobody gave
    it — and until the fact is here, no check can contradict it.

    The pricing rule carries the arithmetic already done. That is deliberate:
    asking a language model to verify 1050/1799 at review time is asking it to do
    the thing it is worst at, on the number that matters most. Computed once
    here, the check downstream is a string comparison.
    """
    if product is None:
        return []

    rules: list[GuardrailRule] = []

    def add(suffix: str, text: str) -> None:
        rules.append(
            GuardrailRule(id=f"product.{suffix}", rule=text,
                          source=SOURCE_PRODUCT, scope=SCOPE_CAMPAIGN)
        )

    identity = [
        f"{label}: {_clean(getattr(product, field, None))}"
        for field, label in (("name", "Name"), ("category", "Category"),
                             ("description", "Description"))
        if _clean(getattr(product, field, None))
    ]
    if identity:
        add("identity",
            "Every asset must show and describe this exact product — " + "; ".join(identity))

    # One rule per attribute rather than one rule listing them all: a verdict
    # names the rule it breached, and "product.attributes" would tell a human
    # that something among eight facts was wrong without saying which.
    for key, value in (product.clean_attributes() if hasattr(product, "clean_attributes") else {}).items():
        add(f"attr.{_slug(key)}",
            f"{key} is {value}. Copy must not contradict that, and imagery must not "
            f"depict something inconsistent with it.")

    for axis, values in (product.clean_options() if hasattr(product, "clean_options") else {}).items():
        add(f"option.{_slug(axis)}",
            f"The only {axis} values that exist are: {', '.join(values)}. Never state, "
            f"imply or depict a {axis} outside that list, and never claim a range the "
            "list does not cover.")

    price_line = product.price_line() if hasattr(product, "price_line") else ""
    if price_line:
        stated = product.stated_discount()
        text = (
            f"When price appears it must read exactly: {price_line}. "
            "Never a different figure, and never a rounded or approximated one."
        )
        if stated is not None:
            text += (
                f" The discount is {stated}%. Never state a higher percentage — "
                "overstating a saving is the direction that causes real harm."
            )
        add("pricing", text)

        conflict = product.discount_conflict()
        if conflict:
            # Surfaced as a rule rather than swallowed. A wrong percentage in the
            # request would otherwise be printed on every asset in the campaign.
            add("pricing_conflict",
                f"The supplied product figures disagree: {conflict}. The computed "
                "figure above is authoritative; a human should correct the input.")

    add("claims",
        "Never state a price, discount, specification, availability, guarantee or "
        "any other fact about the product that the details above do not give you. "
        "If something is absent, leave it out rather than inventing a plausible "
        "value — an invented fact reads exactly like a real one.")

    return rules


def derive(brand: Any = None, project: Any = None, product: Any = None) -> GuardrailSet:
    """
    The rules that follow directly from the brand and project fields supplied.

    Deterministic on purpose: the same brand always yields the same rules with
    the same ids, so a caller that loses the set does not silently get a
    different standard for the derived half. Only inferred rules and human
    amendments are unrecoverable, and those are exactly what round-tripping
    protects.
    """
    rules: list[GuardrailRule] = []

    for field, template in _BRAND_RULE_TEMPLATES.items():
        value = _clean(getattr(brand, field, None) if brand is not None else None)
        if value:
            rules.append(
                GuardrailRule(
                    id=f"brand.{field}",
                    rule=template.format(value=value),
                    source=SOURCE_BRAND,
                    scope=SCOPE_CAMPAIGN,
                )
            )

    for field, template in _PROJECT_RULE_TEMPLATES.items():
        value = _clean(getattr(project, field, None) if project is not None else None)
        if value:
            rules.append(
                GuardrailRule(
                    id=f"project.{field}",
                    rule=template.format(value=value),
                    source=SOURCE_PROJECT,
                    scope=SCOPE_CAMPAIGN,
                )
            )

    rules.extend(_product_rules(product))
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
    except Exception:  # noqa: BLE001 — a malformed rule must not kill a run
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
        # A duplicate id would make an amendment ambiguous — first wins.
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
    brand: Any = None,
    project: Any = None,
    product: Any = None,
) -> GuardrailSet:
    """
    The set this call should use.

    A supplied set is authoritative and is returned as given — it may carry human
    amendments and confirmed inferences that exist nowhere else. Only when none
    was supplied is one derived.
    """
    existing = coerce_set(supplied)
    if existing is not None and existing.rules:
        return existing
    return derive(brand, project, product)


__all__ = [
    "ALL_SCOPES",
    "ALL_SOURCES",
    "GUARDRAIL_VERSION",
    "GuardrailRule",
    "GuardrailSet",
    "coerce_rule",
    "coerce_set",
    "derive",
    "resolve",
    "SCOPE_ASSET_TYPE",
    "SCOPE_CAMPAIGN",
    "SCOPE_SEGMENT",
    "SOURCE_BRAND",
    "SOURCE_HUMAN",
    "SOURCE_INFERRED",
    "SOURCE_PRODUCT",
    "SOURCE_PROJECT",
]
