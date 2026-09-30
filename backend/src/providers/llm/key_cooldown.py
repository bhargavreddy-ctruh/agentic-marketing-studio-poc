"""
Shared in-memory rate-limit cooldown tracker for Groq API keys — used by `groq.py` and
`vision.py`, which both split `GROQ_API_KEY` on commas and independently loop "try each key once,
move on around a 429" (see each file's own docstring).

Real, live-found gap: neither loop remembered which key had JUST been rate-limited, so a key that
failed with a 429 a second ago was tried again FIRST (and failed again) on the very next call —
burning one wasted attempt (per model, in `groq.py`'s `for model in models` case) every single
call until that key's real rate-limit window passed on its own, with no memory of it in between.

Deliberately a soft ordering hint, never a hard block — a cooling-down key is still tried if every
other key is ALSO cooling down (see call sites' `sorted(keys, key=is_cooling_down)`), so an
over-long cooldown window can never fully starve a call as long as at least one key still works.

In-memory only (module-level dict, not persisted) — correct enough for a single backend process;
a restart or a second process just forgets any cooldown, costing one wasted retry, not a
correctness bug.
"""
from __future__ import annotations

import time

_COOLDOWN_SECONDS = 30.0  # Groq's published free-tier limits are per-minute; half that window
# skips the immediate next call without over-penalizing a key whose limit likely already reset by
# the call after that.

_cooldown_until: dict[str, float] = {}


def is_cooling_down(key: str) -> bool:
    """True if `key` was marked rate-limited within the last `_COOLDOWN_SECONDS`."""
    expiry = _cooldown_until.get(key)
    return expiry is not None and time.monotonic() < expiry


def mark_rate_limited(key: str) -> None:
    """Record that `key` just got a real 429, starting its cooldown window."""
    _cooldown_until[key] = time.monotonic() + _COOLDOWN_SECONDS
