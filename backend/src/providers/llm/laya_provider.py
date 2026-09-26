from __future__ import annotations

from typing import Any

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
    async def predict(cls, state: str | dict, questions: dict[str, Any]) -> dict[str, Any]:
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
    async def predict_choice(
        cls,
        state: str | dict,
        options: list[str],
        instructions: str = "Choose the option that best fits this state.",
    ) -> str | None:
        """Helper to get a single choice prediction.

        Real, live-found bug (2026-09-23), three layered mismatches against the installed `laya`
        library (0.3.7), all silently swallowed by `predict()`'s broad `except Exception` (or, for
        the third, by a `.get()` on the wrong key returning `None`) — Laya's shadow-mode routing
        check and its LLM-down fallback path have never actually run in this codebase; they only
        ever no-op'd:
        1. Every question dict must carry an `"instructions"` key — `Agent._check_question` raises
           `ValueError("question 'q1': no 'instructions'; ...")` otherwise. Never set here before.
        2. A `"choice"` question's options go under `"criteria"`, not `"options"`.
        3. `agent.predict()`'s real return shape is `{"model", "answers": {"q1": {...}}, "usage"}`
           — the per-question result is nested under `"answers"`, not a top-level key — and that
           per-question dict's picked value is under `"choice"`, not `"label"`.
        """
        questions = {
            "q1": {
                "type": "choice",
                "instructions": instructions,
                "criteria": list(options),
            }
        }
        res = await cls.predict(state, questions)
        q1_res = (res.get("answers") or {}).get("q1")
        if q1_res and q1_res.get("choice"):
            return q1_res["choice"]
        return None

    @classmethod
    async def predict_noul(cls, state: str | dict, statement_context: str = "") -> float:
        """Helper to get a probability (Noul). Returns 0.0 - 1.0.

        Same real bug family as `predict_choice` above: a `"noul"` question also requires
        `"instructions"` (the statement being evaluated) — this used to send `"context"`, a key the
        library never reads; the result is nested under `res["answers"]["q1"]`, not `res["q1"]`;
        and the probability field is `"noul"`, not `"p_true"`. All four combined meant every real
        call raised or missed, silently falling back to the 0.5 default — `alignment_checker.py`'s
        compliance check has never actually flagged anything real.
        """
        questions = {
            "q1": {
                "type": "noul",
                "instructions": statement_context or "Is this statement true of the given state?",
            }
        }
        res = await cls.predict(state, questions)
        q1_res = (res.get("answers") or {}).get("q1")
        if q1_res and "noul" in q1_res:
            return float(q1_res["noul"])
        return 0.5
