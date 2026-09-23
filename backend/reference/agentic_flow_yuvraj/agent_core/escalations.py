"""
escalations.py — Answering an escalation, rather than only raising one.

A check that exhausts its retries raises an escalation and blocks finalize. That
was the whole of it: the escalation offered `["regenerate", "accept_anyway",
"amend_rule"]` and nothing in the system accepted any of those answers. A person
was shown three options and could act on none of them, so the only way past a
failing check was to stop sending the thing that failed.

Three decisions, and they differ in what they change:

* `regenerate` — nothing. Every request is independent and retry budgets start
  fresh, so trying again *is* re-posting. It exists so a caller can record that a
  person chose it, and so the option list is not two things and a lie.
* `accept_anyway` — the human overrules the check for this subject. Finalize
  stops refusing. The override is carried in the response, because work that
  shipped over a failed check should be visible as such later, not silently
  identical to work that passed.
* `amend_rule` — the rule was wrong, not the work. The guardrail is replaced and
  the check runs again against the new one. This is the only decision that
  changes what future work is judged by, which is why the amendment lands in the
  guardrail set the caller round-trips rather than living for one request.

`reason` is required on `accept_anyway`. An override with no stated reason is the
one entry in an audit trail nobody can act on afterwards.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

REGENERATE = "regenerate"
ACCEPT_ANYWAY = "accept_anyway"
AMEND_RULE = "amend_rule"

ALL_DECISIONS = (REGENERATE, ACCEPT_ANYWAY, AMEND_RULE)


class RuleAmendment(BaseModel):
    """The replacement text for a guardrail a human judged wrong."""

    id: str
    rule: str
    scope: Optional[str] = None
    applies_to: Optional[str] = None

    class Config:
        extra = "allow"


class EscalationDecision(BaseModel):
    """One human answer to one escalation."""

    target: str                       # which check gave up: qa, guardian, logo
    subject: Optional[str] = None     # the scene or stage it gave up on
    decision: str = REGENERATE
    reason: str = ""
    # Caller-supplied and unverified. Recorded so an override is attributable at
    # all; this is not authentication and the contract says so.
    actor: Optional[str] = None
    rule: Optional[RuleAmendment] = None

    class Config:
        extra = "allow"

    def is_override(self) -> bool:
        return self.decision == ACCEPT_ANYWAY

    def is_amendment(self) -> bool:
        return self.decision == AMEND_RULE and self.rule is not None


class EscalationDecisions:
    """
    Decisions the caller has already supplied, keyed on (target, subject).

    Same shape as gate approvals on purpose: a caller that learned one round-trip
    should not have to learn a second. A decision with no subject answers every
    subject of that target, which is what "accept all the brand failures on this
    run" looks like.
    """

    def __init__(self, decisions: Iterable[Any] | None = None):
        self._by_key: dict[tuple[str, str | None], EscalationDecision] = {}
        for raw in decisions or ():
            decision = _coerce(raw)
            if decision is None:
                continue
            self._by_key[(decision.target, decision.subject)] = decision

    def __bool__(self) -> bool:
        return bool(self._by_key)

    def find(self, target: str, subject: str | None = None) -> EscalationDecision | None:
        # An answer naming the subject beats a blanket one for the target.
        if subject is not None:
            hit = self._by_key.get((target, subject))
            if hit is not None:
                return hit
        return self._by_key.get((target, None))

    def accepted(self, target: str, subject: str | None = None) -> EscalationDecision | None:
        """The override for this subject, if a human recorded one."""
        decision = self.find(target, subject)
        return decision if decision is not None and decision.is_override() else None

    def amendments(self) -> list[RuleAmendment]:
        """Every rule a human rewrote, across all targets."""
        out: list[RuleAmendment] = []
        for decision in self._by_key.values():
            if decision.is_amendment() and decision.rule is not None:
                out.append(decision.rule)
        return out

    def all(self) -> list[EscalationDecision]:
        return list(self._by_key.values())


def _coerce(raw: Any) -> EscalationDecision | None:
    if isinstance(raw, EscalationDecision):
        decision = raw
    elif isinstance(raw, dict):
        try:
            decision = EscalationDecision(**raw)
        except Exception:  # noqa: BLE001 — a malformed decision must not kill a run
            return None
    else:
        return None

    if not decision.target:
        return None
    if decision.decision not in ALL_DECISIONS:
        return None
    # An override with no reason is refused rather than silently honoured: it
    # would ship work over a failed check and leave nothing explaining why.
    if decision.is_override() and not (decision.reason or "").strip():
        return None
    if decision.decision == AMEND_RULE and decision.rule is None:
        return None
    return decision


__all__ = [
    "ACCEPT_ANYWAY",
    "ALL_DECISIONS",
    "AMEND_RULE",
    "REGENERATE",
    "EscalationDecision",
    "EscalationDecisions",
    "RuleAmendment",
]
