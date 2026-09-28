"""
Shared sentinel for "no real description was ever captured for this element."

Used in two very different contexts, and the distinction matters:
- Shown as honest prose in an LLM's OWN reasoning context (`ideation_service.py`, `graph.py`,
  `orchestrator.py`, `leads/base.py`) — perfectly fine there; a reasoning model understands it as
  "I don't have a description for this," not as literal content.
- Injected into an actual image-generation PROMPT string handed directly to an image model
  (`base_image_generator.py`) — NEVER fine there. An image model has no way to recognize this is a
  placeholder; it renders whatever text it's given as literal visual instruction. A real,
  live-found bug: a saved generation prompt ended with "...Visually ground the generation in this
  reference: (no description recorded)." — sent verbatim to Qwen.
"""
from __future__ import annotations

NO_DESCRIPTION_SENTINEL = "(no description recorded)"


def is_real_description(text: str | None) -> bool:
    """True only for a genuine, non-empty, non-placeholder description — never true for the
    sentinel above, regardless of how it got here."""
    return bool(text) and text != NO_DESCRIPTION_SENTINEL
