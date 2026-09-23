"""
gates.py — Human-in-the-loop gate matching and decisions.

A gate is a point where the backend stops and hands control back rather than
letting work continue. Two things make this design work:

* **The check lives in the handler, not the prompt.** A model that forgets an
  instruction fails safe, because the provider call is unreachable without an
  approval on record.
* **Nothing pauses.** A gate ends the request. The caller holds the context and
  calls again with a decision, so a person can take a week without anything
  timing out.

Gate entries are stage names, optionally scoped to one subject:

    "pre_image"             every image node in the workflow
    "pre_image:scene_001"   that scene only

A workflow graph can hold several nodes of the same stage — a lookbook with
four angles, a video chain with three cuts — and a bare stage entry gates all
of them. That is right for a careful campaign and wrong for a routine variant,
so an entry may name its subject instead. One list at two granularities,
deliberately: a separate override map would be a second source of truth able to
contradict the first without saying so.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

# Stage names a gate entry may use.
GATE_CONCEPT = "concept"
GATE_STORYBOARD = "storyboard"
GATE_DIRECTION = "direction"
GATE_PRE_IMAGE = "pre_image"
# Fires after a scene has actually generated — pass or fail, every attempt.
# GATE_PRE_IMAGE approves a prompt before it spends; this approves the result
# it bought. A guardian or QA failure no longer earns itself another automatic
# try: it earns a place in this gate's payload, for a human to accept anyway
# or reject with their own words.
GATE_POST_IMAGE = "post_image"
GATE_COPY = "copy"
GATE_PRE_VIDEO = "pre_video"
GATE_QA_OVERRIDE = "qa_override"

ALL_GATES = (
    GATE_CONCEPT,
    GATE_STORYBOARD,
    GATE_DIRECTION,
    GATE_PRE_IMAGE,
    GATE_POST_IMAGE,
    GATE_COPY,
    GATE_PRE_VIDEO,
    GATE_QA_OVERRIDE,
)

# Sent to the human at every gate.
DECISIONS = ("approve", "approve_with_edits", "reject_and_retry", "abort")


class Decision(BaseModel):
    """One human answer to one gate."""

    gate: str
    subject_id: Optional[str] = None
    decision: str = "approve"
    payload: Optional[dict[str, Any]] = None
    feedback: Optional[str] = None

    class Config:
        extra = "allow"

    def is_approval(self) -> bool:
        return self.decision in ("approve", "approve_with_edits")


def _split(entry: str) -> tuple[str, str | None]:
    stage, _, subject = str(entry).partition(":")
    return stage.strip(), (subject.strip() or None)


class GateSet:
    """Which stages stop, and for which subjects."""

    def __init__(self, entries: Iterable[str] | None = None):
        self._all: set[str] = set()
        self._scoped: set[tuple[str, str]] = set()
        for entry in entries or ():
            stage, subject = _split(entry)
            if not stage:
                continue
            if subject:
                self._scoped.add((stage, subject))
            else:
                self._all.add(stage)

    def __bool__(self) -> bool:
        return bool(self._all or self._scoped)

    def is_gated(self, stage: str, subject_id: str | None = None) -> bool:
        if stage in self._all:
            return True
        if subject_id and (stage, subject_id) in self._scoped:
            return True
        return False

    def entries(self) -> list[str]:
        out = sorted(self._all)
        out += sorted(f"{s}:{sub}" for s, sub in self._scoped)
        return out


class Approvals:
    """
    Decisions the caller has already supplied.

    Keyed on (gate, subject_id) so re-posting a request cannot approve twice and
    a retried call is safe to replay. A decision with no subject answers the
    whole stage.
    """

    def __init__(self, decisions: Iterable[Any] | None = None):
        self._by_key: dict[tuple[str, str | None], Decision] = {}
        for raw in decisions or ():
            decision = _coerce_decision(raw)
            if decision is None:
                continue
            self._by_key[(decision.gate, decision.subject_id)] = decision

    def find(self, stage: str, subject_id: str | None = None) -> Decision | None:
        # An exact subject answer wins over a whole-stage one.
        if subject_id is not None:
            hit = self._by_key.get((stage, subject_id))
            if hit is not None:
                return hit
        return self._by_key.get((stage, None))

    def approved(self, stage: str, subject_id: str | None = None) -> bool:
        decision = self.find(stage, subject_id)
        return bool(decision and decision.is_approval())

    def aborted(self) -> Decision | None:
        for decision in self._by_key.values():
            if decision.decision == "abort":
                return decision
        return None

    def edits_for(self, stage: str, subject_id: str | None = None) -> dict[str, Any] | None:
        """The payload a human substituted for the agent's output, if any."""
        decision = self.find(stage, subject_id)
        if decision and decision.decision == "approve_with_edits" and decision.payload:
            return decision.payload
        return None

    def approved_payload(self, stage: str, subject_id: str | None = None) -> dict[str, Any] | None:
        """
        The exact thing the human approved, when they sent it back.

        Answering a gate means calling the step again, and the agents run again
        with it — so an approval that carries no payload is an approval of
        something the re-run may not reproduce. When the caller echoes what it
        showed the human, the backend can pin that instead of the new draft.
        """
        decision = self.find(stage, subject_id)
        if decision and decision.is_approval() and decision.payload:
            return decision.payload
        return None

    def retry_feedback(self, stage: str, subject_id: str | None = None) -> str | None:
        decision = self.find(stage, subject_id)
        if decision and decision.decision == "reject_and_retry":
            return decision.feedback or "Regenerate with different choices."
        return None


def _coerce_decision(raw: Any) -> Decision | None:
    if isinstance(raw, Decision):
        return raw
    if isinstance(raw, dict):
        try:
            return Decision(**raw)
        except Exception:  # noqa: BLE001 — a malformed decision must not kill a run
            return None
    return None


def gate_payload(
    *,
    gate: str,
    subject_id: str | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The shape a gate takes in a response, per CONTRACT.md."""
    return {
        "gate": gate,
        "subject_id": subject_id,
        "payload": payload or {},
        "options": list(DECISIONS),
    }


__all__ = [
    "ALL_GATES",
    "DECISIONS",
    "Approvals",
    "Decision",
    "GateSet",
    "gate_payload",
    "GATE_CONCEPT",
    "GATE_STORYBOARD",
    "GATE_DIRECTION",
    "GATE_PRE_IMAGE",
    "GATE_POST_IMAGE",
    "GATE_COPY",
    "GATE_PRE_VIDEO",
    "GATE_QA_OVERRIDE",
]
