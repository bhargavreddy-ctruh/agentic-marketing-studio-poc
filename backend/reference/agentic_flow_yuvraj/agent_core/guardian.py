"""
guardian.py — Brand-compliance verdicts, kept separate from quality verdicts.

The Critic judges whether an asset is *good*: the product matches its reference,
the scene matches the brief, nothing is warped or misspelled. It says so in its
own prompt that brand compliance is out of scope. Nothing judged that, so a
campaign could pass every check and still be off-brand.

The Guardian judges the other question: whether the work matches the brand it is
supposed to belong to. Two reasons the verdicts stay separate rather than folding
into one score:

* **They fail for different reasons and are fixed differently.** A warped handle
  needs a regenerated image; the wrong palette needs a corrected prompt. One
  merged score cannot say which, and a caller cannot route it.
* **One would swallow the other.** A single `passed` hides that an asset is
  beautiful and off-brand — the case most likely to ship by accident.

A target is the kind of thing being judged. The vocabulary is fixed because a
verdict is only meaningful against a known subject: a model free to invent
targets produces verdicts nothing can act on.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

# What the Guardian can be pointed at.
TARGET_CONCEPT = "concept"
TARGET_STORYBOARD = "storyboard"
TARGET_DIRECTION = "direction"
TARGET_IMAGE = "image"
TARGET_COPY = "copy"
TARGET_VIDEO = "video"
# The mark itself, judged against the supplied logo rather than against a
# description of it. Separate from `image` because it asks a different question
# and answers it by comparison: is this our mark, and is it placed legally.
TARGET_LOGO = "logo"

ALL_TARGETS = (
    TARGET_CONCEPT,
    TARGET_STORYBOARD,
    TARGET_DIRECTION,
    TARGET_IMAGE,
    TARGET_COPY,
    TARGET_VIDEO,
    TARGET_LOGO,
)

# Targets whose subject is something to look at rather than something to read.
VISUAL_TARGETS = (TARGET_IMAGE, TARGET_VIDEO, TARGET_LOGO)


class Violation(BaseModel):
    """
    One specific way the work departs from the brand.

    `expected` and `observed` are both required on purpose. "Off-brand colours"
    is not actionable; "expected #FFD232, observed a muted ochre" tells the next
    attempt what to change, and tells a human whether the Guardian is right.

    `rule_id` names the guardrail breached when there is a rule set. It is what
    lets a human amend the one rule a verdict keeps tripping over instead of
    arguing with the verdict.
    """

    field: str
    expected: str = ""
    observed: str = ""
    rule_id: Optional[str] = None

    class Config:
        extra = "allow"

    def as_line(self) -> str:
        head = f"{self.rule_id} ({self.field})" if self.rule_id else self.field
        parts = [head]
        if self.expected:
            parts.append(f"expected {self.expected}")
        if self.observed:
            parts.append(f"observed {self.observed}")
        return " — ".join(parts)


class GuardianVerdict(BaseModel):
    target: str
    subject_id: Optional[str] = None
    passed: bool = True
    feedback: str = ""
    violations: list[Violation] = Field(default_factory=list)

    class Config:
        extra = "allow"

    def failed(self) -> bool:
        return not self.passed

    def as_feedback(self) -> str:
        """
        The correction text handed to whatever produced the work.

        Built from the violations rather than the summary: the summary is written
        for a human, the violations name the fields to change.
        """
        lines = [v.as_line() for v in self.violations if v.field]
        if lines:
            listed = "\n".join(f"- {line}" for line in lines)
            return f"{self.feedback}\n{listed}".strip() if self.feedback else listed
        return self.feedback


def coerce_verdict(raw: Any) -> GuardianVerdict | None:
    if isinstance(raw, GuardianVerdict):
        return raw
    if isinstance(raw, dict):
        try:
            return GuardianVerdict(**raw)
        except Exception:  # noqa: BLE001 — a malformed verdict must not kill a run
            return None
    return None


def worst_of(verdicts: Iterable[Any]) -> GuardianVerdict | None:
    """
    A single verdict for a step that judged several subjects.

    The first failure wins rather than a count or an average: a step is
    off-brand if any part of it is, and averaging would let three compliant
    scenes outvote one that puts the logo on yellow.
    """
    seen: list[GuardianVerdict] = []
    for raw in verdicts or ():
        verdict = coerce_verdict(raw)
        if verdict is None:
            continue
        if verdict.failed():
            return verdict
        seen.append(verdict)
    return seen[0] if seen else None


__all__ = [
    "ALL_TARGETS",
    "VISUAL_TARGETS",
    "GuardianVerdict",
    "Violation",
    "coerce_verdict",
    "worst_of",
    "TARGET_CONCEPT",
    "TARGET_STORYBOARD",
    "TARGET_DIRECTION",
    "TARGET_IMAGE",
    "TARGET_COPY",
    "TARGET_VIDEO",
    "TARGET_LOGO",
]
