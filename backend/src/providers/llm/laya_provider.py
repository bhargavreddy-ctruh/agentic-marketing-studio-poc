from __future__ import annotations
from typing import Any
import re
from ...core.middleware.logging import get_logger
from .gemini import GeminiProvider
from .base import ModelTier

log = get_logger(__name__)

class LayaProvider:
    """
    Replaced the local Laya Decision Model with GeminiProvider as requested by the user.
    """

    @classmethod
    async def predict_choice(
        cls,
        state: str | dict,
        options: list[str],
        instructions: str = "Choose the option that best fits this state.",
    ) -> str | None:
        llm = GeminiProvider()
        prompt = f"{instructions}\n\nState:\n{state}\n\nOptions:\n{options}\n\nReply ONLY with the exact option text you chose, nothing else."
        try:
            res = await llm.complete(
                tier=ModelTier.TIER_2,
                system="You are an expert decision maker. You only return exact options.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50
            )
            choice = res.content.strip()
            for opt in options:
                if opt.lower() in choice.lower():
                    return opt
        except Exception as e:
            log.warning("gemini_prediction_failed", extra={"_extra_error": str(e)})
        return None

    @classmethod
    async def predict_noul(cls, state: str | dict, statement_context: str = "") -> float:
        llm = GeminiProvider()
        prompt = f"{statement_context or 'Is this statement true of the given state?'}\n\nState:\n{state}\n\nReply ONLY with a float between 0.0 and 1.0 representing the probability, nothing else."
        try:
            res = await llm.complete(
                tier=ModelTier.TIER_2,
                system="You are an expert probability estimator. Return only a float.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10
            )
            match = re.search(r"0\.\d+|1\.0", res.content)
            if match:
                return float(match.group(0))
        except Exception as e:
            log.warning("gemini_noul_prediction_failed", extra={"_extra_error": str(e)})
        return 0.5
