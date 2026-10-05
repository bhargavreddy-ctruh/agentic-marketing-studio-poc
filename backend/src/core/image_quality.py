"""
Shared image-quality baseline (Memory.md fidelity audit, 2026-10-05): none of the image-generation
tools ever applied a default negative prompt — `negative_prompt` was opt-in only, triggered solely
by the LLM noticing an explicit user exclusion. `merge_negative_prompt` gives every image tool the
same cheap, deterministic quality floor without requiring the LLM to remember to ask for one.
"""
from __future__ import annotations

DEFAULT_NEGATIVE_PROMPT = (
    "blurry, low-resolution, amateur, watermark, distorted logo, plastic skin, waxy skin, "
    "airbrushed, mannequin-like, uncanny valley, mask-like face, oversaturated, extra fingers, "
    "fused fingers, mutated hands, bad anatomy, disconnected hair, misaligned teeth, "
    "generic stock photo look, flat lighting"
)


def merge_negative_prompt(user_negative_prompt: str | None) -> str:
    """Appends the LLM's own exclusions (if any) onto the standing quality baseline, rather than
    letting one silently replace the other. For providers with a real `negative_prompt` field
    (Qwen-Image family)."""
    user_part = (user_negative_prompt or "").strip()
    if not user_part:
        return DEFAULT_NEGATIVE_PROMPT
    return f"{DEFAULT_NEGATIVE_PROMPT}, {user_part}"


def append_quality_guard_to_prompt(prompt: str, user_negative_prompt: str | None = None) -> str:
    """For providers with NO real `negative_prompt` field in their actual vendor schema (confirmed:
    both Nano Banana providers — Gemini-family image models don't expose one on Replicate) — folds
    the same quality baseline into the prompt text itself as a natural-language exclusion clause,
    since that's the only lever these specific models accept."""
    exclusions = merge_negative_prompt(user_negative_prompt)
    return f"{prompt}\n\nAvoid: {exclusions}."
