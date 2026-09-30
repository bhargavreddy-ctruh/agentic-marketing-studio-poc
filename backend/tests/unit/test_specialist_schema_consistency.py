"""
Real, live-found gap (2026-09-30, JSON-parsing audit): nothing ever verified that a specialist's
`required_output_fields` (registry.py, code-enforced by runner.py) actually matches what its own
prompt file's `<output_format>` block promises to the model. A drift in either direction was
previously undetected until a live failure:
  - registry.py requires a field the prompt never declares -> runner.py retries forever asking
    for a field the model was never told to produce.
  - the prompt promises a field registry.py never requires -> a response missing it is silently
    accepted, never validated.
  - a specialist has no `<output_format>` block at all (a genuine, valid design choice - e.g.
    `brand_asset_applier`, which only ever reports success via its tool calls) but its registry
    entry still declares required fields -> runner.py would force JSON-parsing on a specialist
    whose own prompt never asked for it (the exact class of bug fixed 2026-09-30 in runner.py's
    `if not spec.required_output_fields` bypass).

Writing this test against every CURRENT specialist confirmed one real, pre-existing bug in the
process: `sound_designer.md`'s example schema had `"audio_recommendation": "voiceover" or
"silent"` - not valid JSON at all (a bare `or` between two string literals) - now fixed to match
the `"a | b | c"` convention every other specialist's prompt already uses.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.services.specialists.registry import SPECIALIST_REGISTRY, load_all_specialists

load_all_specialists()

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "src" / "services" / "specialists" / "prompts"


def _extract_output_format_keys(prompt_text: str) -> set[str] | None:
    """The top-level JSON keys declared in a prompt's <output_format> block, or None if there's no
    such block at all. Real schema is the LAST top-level {...} span in the block (the first is
    often the `{"error": "..."}` escape-hatch example) - found by tracking brace depth across the
    whole block rather than naively matching from the last `{` CHARACTER, which breaks the moment
    the real schema itself contains a nested object/array-of-objects (tone_calibrator's
    `tone_profile`, copy_claims_checker's `flagged_claims` entries)."""
    match = re.search(r"<output_format>([\s\S]*?)</output_format>", prompt_text)
    if not match:
        return None
    block = match.group(1)

    spans: list[tuple[int, int]] = []
    depth = 0
    start: int | None = None
    for i, ch in enumerate(block):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, i))
    if not spans:
        return None

    schema_start, schema_end = spans[-1]
    parsed = json.loads(block[schema_start : schema_end + 1])
    assert isinstance(parsed, dict)
    return set(parsed.keys())


@pytest.mark.parametrize("specialist_name", sorted(SPECIALIST_REGISTRY.keys()))
def test_required_output_fields_matches_prompt_schema(specialist_name: str) -> None:
    spec = SPECIALIST_REGISTRY[specialist_name]
    prompt_text = (_PROMPTS_DIR / spec.prompt_file).read_text(encoding="utf-8")
    declared_keys = _extract_output_format_keys(prompt_text)

    if declared_keys is None:
        # No <output_format> block at all - a genuine, valid design choice (e.g.
        # brand_asset_applier), but only when the registry entry agrees there's no schema to
        # enforce. runner.py's own JSON-bypass logic depends on this exact pairing being correct.
        assert not spec.required_output_fields, (
            f"{specialist_name}: prompt file {spec.prompt_file} has no <output_format> block, but "
            f"registry.py declares required_output_fields={spec.required_output_fields!r} — "
            f"runner.py will force JSON-parsing on a response this prompt never asked for."
        )
        return

    required = set(spec.required_output_fields)
    missing_from_prompt = required - declared_keys
    assert not missing_from_prompt, (
        f"{specialist_name}: registry.py requires {missing_from_prompt}, but the prompt's "
        f"<output_format> schema in {spec.prompt_file} never declares it."
    )
    missing_from_registry = declared_keys - required
    assert not missing_from_registry, (
        f"{specialist_name}: the prompt's <output_format> schema in {spec.prompt_file} promises "
        f"{missing_from_registry}, but registry.py's required_output_fields never validates it — "
        f"a response missing that field would be silently accepted."
    )
