"""
Real per-stage/per-edit approval detection — Memory.md, Phase 4 "approve" mode. A user's response
to a pending proposal is either an approval or feedback requesting changes.

Defaults to "not approved" (i.e. treat as revision feedback) on anything ambiguous — never
silently treats an unclear reply as approval (Rules.md section 2: no silently defaulting to a
real-looking value; an unrecognized answer should never be mistaken for a green light to proceed).
"""
from __future__ import annotations

# Real, live-found bug (2026-09-22): "yes" used to live in the SAME startswith-matched phrase list
# as longer phrases like "approve"/"perfect" — but "yes" is short enough to be a real prefix of
# totally unrelated words ("yesterday I want a red car" would have matched `startswith("yes")` as
# an approval). Split into an EXACT-match-only set for short, ambiguous-as-a-prefix words, and a
# startswith-matched set for longer phrases where that risk doesn't exist.
_APPROVE_EXACT = {"yes", "yeah", "yep"}
_APPROVE_PHRASES = (
    "approve", "approved", "looks good", "looks great", "good to go",
    "ship it", "perfect", "lgtm", "sounds good",
)

# Real, explicit escape hatch (2026-09-22) — a real live-found gap: the narrative/scene approval
# gates only ever offered "Approve"/"Request changes", with no way to reject a proposal outright.
# A user stuck with a genuinely wrong proposal had no option but to keep sending "revise" feedback
# against it forever, each round still anchored to the same original (possibly wrong) context —
# there was never a way to actually discard it and start over clean.
#
# `_CANCEL_EXACT` added 2026-09-22 (same session, a second real bug traced from the first): a bare
# "no" reply — the direct, obvious counterpart to "yes" above — matched NEITHER `is_approval` nor
# `is_cancel` at all, so a real "no, keep the existing price" reply to a yes/no ideation question
# fell through unrecognized (`session_service.py`'s `_resolve_yes_no_reply`, which this pairs with).
# Exact-match only, same reasoning as `_APPROVE_EXACT` — "no" is a real prefix of "nothing"/
# "november"/etc., so it must never be startswith-matched.
_CANCEL_EXACT = {"no", "nope", "nah"}
_CANCEL_PHRASES = (
    "cancel", "reject", "stop", "nevermind", "never mind", "start over", "discard", "abort",
)


def is_approval(message: str) -> bool:
    text = (message or "").strip().lower()
    return text in _APPROVE_EXACT or any(text.startswith(phrase) for phrase in _APPROVE_PHRASES)


def is_cancel(message: str) -> bool:
    text = (message or "").strip().lower()
    return text in _CANCEL_EXACT or any(text.startswith(phrase) for phrase in _CANCEL_PHRASES)
