from __future__ import annotations

import logging
from typing import Any, Dict

import laya

from ...core.middleware.logging import get_logger

log = get_logger(__name__)

class LayaProvider:
    """
    Singleton wrapper for the Laya Decision Model (running locally).
    Downloads and caches the convaiinnovations/laya model weights into memory.
    """
    _agent = None

    @classmethod
    def get_agent(cls):
        if cls._agent is None:
            log.info("Loading Laya model into memory...")
            try:
                # Load the base english model, downloading if necessary.
                cls._agent = laya.load("convaiinnovations/laya")
            except Exception as e:
                log.error("Failed to load Laya model", extra={"_extra_error": str(e)})
                raise
        return cls._agent

    @classmethod
    async def predict(cls, state: str | dict, questions: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs a structured decision prediction using Laya.
        """
        agent = cls.get_agent()
        try:
            # Laya predict is synchronous and fast, but we might want to wrap it in an asyncio 
            # threadpool if we start doing large batches. For now, running directly is fine.
            return agent.predict(state=state, questions=questions)
        except Exception as e:
            log.warning("laya_prediction_failed", extra={"_extra_error": str(e)})
            return {}

    @classmethod
    async def predict_choice(cls, state: str | dict, options: list[str]) -> str | None:
        """Helper to get a single choice prediction."""
        questions = {
            "q1": {
                "type": "choice",
                "options": options
            }
        }
        res = await cls.predict(state, questions)
        q1_res = res.get("q1")
        if q1_res and q1_res.get("label"):
            return q1_res["label"]
        return None

    @classmethod
    async def predict_noul(cls, state: str | dict, statement_context: str = "") -> float:
        """Helper to get a probability (Noul). Returns 0.0 - 1.0"""
        questions = {
            "q1": {
                "type": "noul",
                "context": statement_context
            }
        }
        res = await cls.predict(state, questions)
        q1_res = res.get("q1")
        if q1_res and "p_true" in q1_res:
            return float(q1_res["p_true"])
        return 0.5
