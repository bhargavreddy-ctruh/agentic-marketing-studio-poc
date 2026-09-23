"""
brand_context.py — Typed brand and project context shared by the agent services.

Brand details arrive as structured fields on the request. Nothing is stored and
nothing is inferred: this layer is a schema, validation and a renderer, so the
context an agent sees is reproducible rather than model-authored.

Two rules shape the renderer:

* **Skip what is absent.** Every field is optional. Emitting "Voice & tone: None"
  spends tokens telling a model nothing and invites it to invent a value.
* **Give each role its slice.** Marketing needs voice and messaging pillars; the
  image side needs visual identity. Sending everything to everyone costs tokens
  and dilutes attention.

Output is wrapped in tagged sections so a model can tell supplied data from its
own instructions. These fields are user-written or scraped, and every agent
prompt declares them untrusted; the tags are what that declaration points at.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

# Roles that consume context. Keep in step with the agents in campaign_studio.
CREATIVE_DIRECTOR = "creative_director"
STORYBOARD_DESIGNER = "storyboard_designer"
ART_DIRECTOR = "art_director"
MARKETING = "marketing"
CRITIC = "qa"
GUARDIAN = "guardian"
VIDEO_DIRECTOR = "video_director"
# Outside campaign_studio: agents that dress a space rather than shoot a campaign.
STORE_COMPOSER = "store_composer"

_BRAND_LABELS: dict[str, str] = {
    "voice_and_tone": "Voice and tone",
    "visual_identity": "Visual identity",
    "positioning_and_pillars": "Positioning and messaging pillars",
    "audience_segments": "Audience segments",
    "category_context": "Category and competitive context",
    "logo_rules": "Logo rules",
}

_PROJECT_LABELS: dict[str, str] = {
    "goal": "Goal",
    "audience": "Audience",
    "campaign_description": "Campaign description",
}

# role -> (brand fields, project fields). A role missing here gets everything,
# which is the safe direction to fail: too much context is a cost, too little is
# a wrong answer.
_RELEVANCE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    CREATIVE_DIRECTOR: (tuple(_BRAND_LABELS), tuple(_PROJECT_LABELS)),
    STORYBOARD_DESIGNER: (
        ("visual_identity", "audience_segments", "category_context"),
        ("goal", "audience", "campaign_description"),
    ),
    ART_DIRECTOR: (("visual_identity", "logo_rules"), ()),
    MARKETING: (
        ("voice_and_tone", "positioning_and_pillars", "audience_segments"),
        ("goal", "audience"),
    ),
    # The Critic judges the scene against the product and the brief, not the
    # brand — the guardian owns brand. Giving it brand fields invites it back
    # into territory the last merge deliberately took away from it.
    CRITIC: ((), ("campaign_description",)),
    GUARDIAN: (tuple(_BRAND_LABELS), ("goal", "audience")),
    VIDEO_DIRECTOR: (("visual_identity", "voice_and_tone"), ("goal",)),
    # Builds a branded space: it picks palettes, templates and signage, so it
    # needs how the brand looks and how it speaks, not its media strategy.
    STORE_COMPOSER: (
        ("visual_identity", "logo_rules", "voice_and_tone"),
        ("goal", "audience"),
    ),
    # Deliberately absent, and so getting everything: the flow builder plans
    # arbitrary graphs and Assist answers arbitrary questions. Neither has a
    # fixed job to slice context against, and guessing one would withhold the
    # field that happened to matter.
}


class LogoAsset(BaseModel):
    """One logo, supplied the way product images already are."""

    url: Optional[str] = None
    base64: Optional[str] = None
    mime_type: str = "image/png"

    def present(self) -> bool:
        return bool(self.url or self.base64)

    def viewable(self) -> dict[str, str] | None:
        """
        The mark as bytes an agent can look at, or None if only a URL is on file.

        A logo rule can only be checked against the actual mark — comparing a
        render to a description of a logo is how a hallucinated wordmark passes.
        A URL has to be fetched at the edge before it gets this far; returning
        None here keeps the check honest rather than judging from prose.
        """
        data = (self.base64 or "").strip()
        if not data:
            return None
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        mime = self.mime_type or "image/png"
        return {"mime_type": "image/jpeg" if mime == "image/jpg" else mime, "data": data}


class BrandDetails(BaseModel):
    voice_and_tone: Optional[str] = None
    visual_identity: Optional[str] = None
    positioning_and_pillars: Optional[str] = None
    audience_segments: Optional[str] = None
    category_context: Optional[str] = None
    logo: Optional[LogoAsset] = None
    logo_rules: Optional[str] = None

    class Config:
        extra = "allow"

    def any_set(self) -> bool:
        return any(_clean(getattr(self, f, None)) for f in _BRAND_LABELS)


class ProjectDetails(BaseModel):
    goal: Optional[str] = None
    audience: Optional[str] = None
    campaign_description: Optional[str] = None

    class Config:
        extra = "allow"

    def any_set(self) -> bool:
        return any(_clean(getattr(self, f, None)) for f in _PROJECT_LABELS)


class ProductDetails(BaseModel):
    """
    What the catalogue says about the thing being sold, whatever it is.

    Not derivable from the product photographs, which is the point: a picture
    shows a caramel-orange tee, and no amount of looking at it yields ₹749 or
    ₹1,799. Those are the facts a caption gets wrong, and until they arrive as
    data nothing can contradict an invented one.

    Deliberately domain-neutral. An earlier draft had `fit`, `sizes` and
    `material` as named fields, which is a clothing catalogue wearing a costume:
    a subscription has plans, a car has a range, a service has a term, and none
    of them have a fit. Two open maps carry all of it, and they are separate
    because the rule they produce is different:

    * `attributes` — things that are true. "Do not contradict these."
    * `options`    — closed lists. "These are the only values that exist."

    The second is what makes "never claim a size we do not sell" expressible, and
    it says the same thing about storage tiers, seat counts and plan names.
    """

    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None

    # True of this product. Any keys: Fit, Material, Range, Term, Warranty…
    attributes: dict[str, str] = Field(default_factory=dict)
    # Axis -> the complete list of values that exist for it. Sizes, colours,
    # plans, capacities, finishes.
    options: dict[str, list[str]] = Field(default_factory=dict)

    currency: str = ""
    price: Optional[float] = None
    # What it was before the offer — list price, RRP, MRP, whatever the market
    # calls it.
    compare_at_price: Optional[float] = None
    # What the caller believes the discount is. Checked against the two prices
    # rather than trusted: a wrong number here prints on every asset.
    discount_percent: Optional[float] = None

    class Config:
        extra = "allow"

    def any_set(self) -> bool:
        return bool(
            self.attributes or self.options
            or any(_clean(getattr(self, f, None)) for f in ("name", "category", "description"))
            or self.price is not None
            or self.compare_at_price is not None
        )

    def clean_attributes(self) -> dict[str, str]:
        return {
            str(k).strip(): _clean(v, limit=300)
            for k, v in (self.attributes or {}).items()
            if str(k).strip() and _clean(v)
        }

    def clean_options(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for axis, values in (self.options or {}).items():
            axis_name = str(axis).strip()
            listed = [str(v).strip() for v in (values or []) if str(v).strip()]
            if axis_name and listed:
                out[axis_name] = listed
        return out

    def exact_discount(self) -> float | None:
        """The real percentage off, to one decimal, or None if it cannot be known."""
        was = self.compare_at_price
        if self.price is None or was is None:
            return None
        if was <= 0 or self.price < 0 or self.price > was:
            return None
        return round((was - self.price) / was * 100, 1)

    def stated_discount(self) -> int | None:
        """
        The percentage an asset should print.

        Floored, not rounded. 1050 off 1799 is 58.37%, and retail says "58% OFF";
        rounding up to 59 overstates the saving, which is the direction that gets
        a brand into trouble rather than merely looking untidy.
        """
        exact = self.exact_discount()
        return int(exact) if exact is not None else None

    def discount_conflict(self) -> str | None:
        """A supplied discount that the prices do not support."""
        stated = self.stated_discount()
        if self.discount_percent is None or stated is None:
            return None
        if abs(self.discount_percent - stated) < 1:
            return None
        return (
            f"supplied discount_percent {self.discount_percent:g}% does not match "
            f"{self._money(self.price)} against {self._money(self.compare_at_price)}, "
            f"which is {stated}%"
        )

    def _money(self, amount: float | None) -> str:
        if amount is None:
            return ""
        cur = (self.currency or "").strip()
        text = f"{amount:g}"
        # A bare symbol butts up against the number; a code needs a space.
        return f"{cur}{text}" if len(cur) == 1 else (f"{cur} {text}" if cur else text)

    def price_line(self) -> str:
        """How the offer must be written, once, so every agent writes it the same."""
        if self.price is None:
            return ""
        parts = [self._money(self.price)]
        was = self.compare_at_price
        if was is not None and was > self.price:
            parts.append(f"reduced from {self._money(was)}")
            stated = self.stated_discount()
            if stated is not None:
                parts.append(f"{stated}% OFF")
        return ", ".join(parts)


def _clean(value: Any, limit: int = 1200) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit] if text else ""


def _section(tag: str, labels: dict[str, str], fields: Iterable[str], src: Any) -> str:
    lines = []
    for field in fields:
        text = _clean(getattr(src, field, None) if src is not None else None)
        if text:
            lines.append(f"{labels[field]}: {text}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"<{tag}>\n{body}\n</{tag}>"


# Roles allowed to state the offer. Everyone else sees the product without it: a
# scene designer who knows the price has everything needed to render a number
# onto an image that nobody asked for.
_PRICING_ROLES = {CREATIVE_DIRECTOR, MARKETING, GUARDIAN, CRITIC}

# Roles that make claims in words, and so need to know which values exist. The
# people composing a picture do not need the list of plan tiers.
_OPTION_ROLES = {CREATIVE_DIRECTOR, MARKETING, GUARDIAN, CRITIC}


def _product_section(product: Any, role: str) -> str:
    """
    The product as this role needs to see it.

    Identity and attributes go to everyone — whoever is depicting or describing
    the thing has to know what it is. Options and pricing go only to the roles
    that make claims, because those are the two that get invented.
    """
    if product is None:
        return ""

    lines: list[str] = []
    for field, label in (("name", "Product"), ("category", "Category"),
                         ("description", "Description")):
        text = _clean(getattr(product, field, None))
        if text:
            lines.append(f"{label}: {text}")

    for key, value in (product.clean_attributes() if hasattr(product, "clean_attributes") else {}).items():
        lines.append(f"{key}: {value}")

    if role in _OPTION_ROLES:
        for axis, values in (product.clean_options() if hasattr(product, "clean_options") else {}).items():
            lines.append(f"{axis} — the only values that exist: {', '.join(values)}")

    if role in _PRICING_ROLES:
        offer = product.price_line() if hasattr(product, "price_line") else ""
        if offer:
            lines.append(f"Offer, to be stated exactly this way: {offer}")
            conflict = product.discount_conflict()
            if conflict:
                lines.append(f"WARNING — the supplied figures disagree: {conflict}")

    if not lines:
        return ""
    body = "\n".join(lines)
    return f"<product_details>\n{body}\n</product_details>"


def render_context(
    *,
    brand: BrandDetails | None,
    project: ProjectDetails | None,
    role: str,
    fallback: str = "",
    product: Any = None,
) -> str:
    """
    Render the slice of context this role needs, or `fallback` if there is none.

    The caller passes the result straight into a prompt, so an empty return has
    to be usable: pass a fallback where the prompt reads better with a sentence
    than with a gap.
    """
    brand_fields, project_fields = _RELEVANCE.get(
        role, (tuple(_BRAND_LABELS), tuple(_PROJECT_LABELS))
    )
    parts = [
        _section("brand_details", _BRAND_LABELS, brand_fields, brand),
        _section("project_details", _PROJECT_LABELS, project_fields, project),
        _product_section(product, role),
    ]
    rendered = "\n".join(p for p in parts if p)
    return rendered or fallback


def context_block(
    *,
    brand: Any = None,
    project: Any = None,
    product: Any = None,
    role: str = "",
    legacy_kit: dict[str, Any] | None = None,
    fallback: str = "No brand context provided.",
    kit_limit: int = 4000,
) -> str:
    """
    The whole brand block for one agent prompt, typed details and legacy kit both.

    Every service that renders brand into a prompt needs the same three rules:
    typed details win, a legacy `brand_kit` dict still renders as it always did,
    and an empty result has to be a usable sentence rather than a gap. Five
    copies of that would drift, so it lives here and each service passes its own
    role and fallback.

    Accepts models or plain dicts — callers get their request bodies in both
    shapes depending on whether FastAPI parsed them.
    """
    import json  # local: this module is otherwise dependency-free

    parts: list[str] = []

    brand_model = coerce_brand(brand)
    project_model = coerce_project(project)
    product_model = coerce_product(product)
    if brand_model is not None or project_model is not None or product_model is not None:
        rendered = render_context(brand=brand_model, project=project_model,
                                  product=product_model, role=role)
        if rendered:
            parts.append(rendered)

    if legacy_kit:
        body = json.dumps(legacy_kit, indent=2, default=str)[:kit_limit]
        parts.append(f"<brand_kit>\n{body}\n</brand_kit>")

    return "\n".join(parts) if parts else fallback


def visual_memory(brand: BrandDetails | None) -> str:
    """
    The part of the brand an image model should be told about.

    Separate from render_context because this reaches a generation provider
    rather than an agent prompt — it has no tags and no role slicing, and it is
    the field set that previously never left the request at all.
    """
    if brand is None:
        return ""
    bits = []
    for field in ("visual_identity", "logo_rules"):
        text = _clean(getattr(brand, field, None), limit=600)
        if text:
            bits.append(text)
    return " ".join(bits)


def coerce_brand(value: Any) -> BrandDetails | None:
    """Accept a model, a dict, or nothing."""
    if value is None:
        return None
    if isinstance(value, BrandDetails):
        return value
    if isinstance(value, dict):
        try:
            return BrandDetails(**value)
        except Exception:  # noqa: BLE001 — malformed context must not kill a run
            return None
    return None


def coerce_product(value: Any) -> ProductDetails | None:
    if value is None:
        return None
    if isinstance(value, ProductDetails):
        return value
    if isinstance(value, dict):
        try:
            return ProductDetails(**value)
        except Exception:  # noqa: BLE001 — malformed context must not kill a run
            return None
    return None


def coerce_project(value: Any) -> ProjectDetails | None:
    if value is None:
        return None
    if isinstance(value, ProjectDetails):
        return value
    if isinstance(value, dict):
        try:
            return ProjectDetails(**value)
        except Exception:  # noqa: BLE001
            return None
    return None


__all__ = [
    "BrandDetails",
    "ProjectDetails",
    "ProductDetails",
    "LogoAsset",
    "render_context",
    "context_block",
    "visual_memory",
    "coerce_brand",
    "coerce_project",
    "coerce_product",
    "CREATIVE_DIRECTOR",
    "STORYBOARD_DESIGNER",
    "ART_DIRECTOR",
    "MARKETING",
    "CRITIC",
    "GUARDIAN",
    "VIDEO_DIRECTOR",
    "STORE_COMPOSER",
]
