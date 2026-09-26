"""
Discount/Claims Calculator tool — Architecture.md section 1b. A deterministic math check against
Product DNA's real, exact stored price/discount fields — never an LLM guess, and not the fuzzy
vector retrieval `product_lookup` uses either (guardrail-first specifically means exact, not
approximately-retrieved — Architecture.md section 1).

Tools are registered once at boot, outside any one HTTP request's DB session, so this is the one
tool that opens its own short-lived session directly against the shared engine
(`models/base.py`'s `async_session_factory`) rather than receiving one via FastAPI's DI — still
only ever through `SqliteProductRepository`, keeping "only repositories touch the database" intact
(Rules.md section 2).
"""
from __future__ import annotations

from typing import ClassVar

from ...models.base import async_session_factory
from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("discount_claims_calculator")
class DiscountClaimsCalculatorTool(Tool):
    name = "discount_claims_calculator"
    description = (
        "Checks a claimed price/discount for a product against its real stored Product DNA "
        "record and returns the exact figures that must be used instead of a guess."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "product_id": {"type": "string"},
            "claimed_price": {"type": "number"},
            "claimed_discount_percent": {"type": "number"},
        },
        "required": ["product_id"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        product_id = str(args.get("product_id") or "").strip()
        if not product_id:
            return ToolResult(ok=False, data={}, error="product_id is required")

        async with async_session_factory() as session:
            product = await SqliteProductRepository(session).get(product_id)

        if product is None:
            return ToolResult(ok=False, data={}, error=f"no product found for product_id {product_id}")

        real_price = product.attributes.get("price")
        real_discount = product.attributes.get("discount_percent")
        claimed_price = args.get("claimed_price")
        claimed_discount = args.get("claimed_discount_percent")

        price_accurate = claimed_price is None or real_price is None or claimed_price == real_price
        discount_accurate = (
            claimed_discount is None or real_discount is None or claimed_discount == real_discount
        )

        return ToolResult(
            ok=True,
            data={
                "real_price": real_price,
                "real_discount_percent": real_discount,
                "price_accurate": price_accurate,
                "discount_accurate": discount_accurate,
                "overlay_text_should_use": _format_overlay_text(real_price, real_discount),
            },
        )


def _format_overlay_text(price: float | None, discount: float | None) -> str:
    parts = []
    if price is not None:
        parts.append(f"${price:g}")
    if discount:
        parts.append(f"{discount:g}% off")
    return " — ".join(parts)
