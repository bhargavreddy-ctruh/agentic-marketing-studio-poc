"""
Discount Math Calculator — Architecture.md section 1b's guardrail-first principle, applied to a
base price + discount percentage the USER stated directly in chat (not a stored Product DNA
record — that case is `discount_claims_calculator.py`'s job). Real, live-found reason this exists
(2026-09-21): asked to overlay "100k with 15% discount", a specialist literally overlaid that raw
phrase instead of the actual final price — the model was never given a real way to compute it, so
it reached for a text-level workaround instead of arithmetic. Deterministic Python, never an LLM's
own mental math, for the exact same reason `discount_claims_calculator` never trusts a model to
recall a stored price correctly.
"""
from __future__ import annotations

from typing import ClassVar

from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("discount_math_calculator")
class DiscountMathCalculatorTool(Tool):
    name = "discount_math_calculator"
    description = (
        "Computes the exact final price from a base price and a discount percentage the user "
        "stated directly in chat — real arithmetic, never a guess or an LLM's own mental math."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "base_price": {"type": "number"},
            "discount_percent": {"type": "number"},
        },
        "required": ["base_price", "discount_percent"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        try:
            base_price = float(args["base_price"])
            discount_percent = float(args["discount_percent"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(ok=False, data={}, error="base_price and discount_percent must be real numbers")

        final_price = round(base_price * (1 - discount_percent / 100), 2)
        return ToolResult(
            ok=True,
            data={
                "base_price": base_price,
                "discount_percent": discount_percent,
                "final_price": final_price,
                "overlay_text_should_use": (
                    f"${final_price:,.0f} ({discount_percent:g}% OFF, was ${base_price:,.0f})"
                ),
            },
        )
