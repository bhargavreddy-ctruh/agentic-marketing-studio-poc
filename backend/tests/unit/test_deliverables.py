"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found bug (2026-10-06): a message naming TWO different deliverables ("make a youtube
thumbnail... and also an instagram 9:16 image post") used to resolve to a single, turn-global
`brief["deliverable"]` via `detect_deliverable_key`'s first-regex-match — the Instagram 9:16
pattern matched before the YouTube thumbnail pattern, so the whole turn (including the thumbnail
step) silently got forced to a 9:16 aspect ratio. `detect_deliverable_keys` (plural) is the fix:
callers check this first and skip setting a single global deliverable spec when it finds 2+.

Real, live-found follow-up (same day): that fix alone wasn't sufficient — `runner.py`'s aspect-
ratio enforcement still fell back to inferring from the WHOLE shared turn message (which still
names both deliverables) rather than from each dynamic-plan step's own, specific instruction text,
so the collision reappeared through this second path. Confirmed live against the real Replicate
prediction: a prompt explicitly titled "YouTube thumbnail" was submitted with `aspect_ratio: 9:16`.
`_context_to_text` (`runner.py`) is the real fix: it extracts ONLY the text after dynamic
executor's `YOUR SPECIFIC INSTRUCTION FOR THIS STEP:` marker (`graph.py:1530`), excluding the
shared "Campaign idea so far" preamble (`graph.py:1420`) that carries every deliverable the turn
ever mentioned.
"""
from src.core.deliverables import detect_deliverable_key, detect_deliverable_keys
from src.services.leads.base import infer_aspect_ratio_from_text
from src.services.specialists.runner import _context_to_text


def test_multi_deliverable_message_detects_both_keys():
    text = (
        "make a thumbnail for youtube video for this, its an unboxing video and also generate "
        "an instagram 9:16 image post for the same"
    )
    keys = detect_deliverable_keys(text)
    assert "youtube_thumbnail" in keys
    assert "instagram_story" in keys
    assert len(keys) >= 2


def test_single_deliverable_message_still_resolves_exactly_one():
    text = "make this a youtube thumbnail"
    keys = detect_deliverable_keys(text)
    assert keys == ["youtube_thumbnail"]
    # The original single-match function is unchanged for the common case.
    assert detect_deliverable_key(text) == "youtube_thumbnail"


def test_no_deliverable_named_returns_empty():
    assert detect_deliverable_keys("make this image pop more") == []
    assert detect_deliverable_key("make this image pop more") is None


def _step_context(shared_preamble: str, step_instruction: str) -> list[dict]:
    """Mirrors graph.py's real shape: one shared text part carrying the turn-wide preamble plus
    this step's own instruction appended after the marker (`_run_one_step`, `graph.py:1530`)."""
    return [{
        "type": "text",
        "text": (
            f"Campaign idea so far:\n{shared_preamble}\n\n"
            "Overall collective goal (do not drift from this): ...\n\n"
            f"YOUR SPECIFIC INSTRUCTION FOR THIS STEP:\n{step_instruction}"
        ),
    }]


def test_step_scoped_aspect_ratio_does_not_collide_across_plan_steps():
    shared_preamble = (
        "make a thumbnail for youtube video for this, its an unboxing video and also generate "
        "an instagram 9:16 image post for the same"
    )
    thumbnail_step = _step_context(
        shared_preamble,
        "Generate a crazy, eye-catching YouTube thumbnail for the unboxing video using the "
        "referenced phone image.",
    )
    instagram_step = _step_context(
        shared_preamble,
        "Generate an Instagram 9:16 image post for the same unboxing using the referenced phone "
        "image.",
    )
    # Without step-scoping, both would resolve to 9:16 (the shared preamble's first regex match).
    assert infer_aspect_ratio_from_text(_context_to_text(thumbnail_step)) == "16:9"
    assert infer_aspect_ratio_from_text(_context_to_text(instagram_step)) == "9:16"


def test_context_to_text_falls_back_to_whole_text_without_the_marker():
    # A direct_fix-style context has no per-step marker — the whole text is used, unchanged.
    assert _context_to_text("just fix the lighting on this") == "just fix the lighting on this"
