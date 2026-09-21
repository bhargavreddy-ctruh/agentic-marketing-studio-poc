"""
Real per-stage/per-edit approval detection — Memory.md, Phase 4 "approve" mode. A user's response
to a pending proposal is either an approval or feedback requesting changes.

Defaults to "not approved" (i.e. treat as revision feedback) on anything ambiguous — never
silently treats an unclear reply as approval (Rules.md section 2: no silently defaulting to a
real-looking value; an unrecognized answer should never be mistaken for a green light to proceed).
"""
from __future__ import annotations

_APPROVE_PHRASES = (
    "approve", "approved", "looks good", "looks great", "good to go", "yes",
    "ship it", "perfect", "lgtm", "sounds good",
)


def is_approval(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(text == phrase or text.startswith(phrase) for phrase in _APPROVE_PHRASES)
