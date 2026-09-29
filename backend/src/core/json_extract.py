"""
Best-effort JSON extraction from LLM text output — free-tier models are more likely than a
frontier model to wrap JSON in prose or a code fence. Ported pattern from the existing
agentic_flow codebase's flow_builder `_extract_json` (Rules.md section 6).
"""
from __future__ import annotations

import json
import re
from typing import Any


def _close_truncated_json(text: str) -> str | None:
    """Real, live-found failure (2026-09-30, palette_strategist): a verbose model response ran
    past its token budget MID-STRING, cutting the response off before the JSON object's closing
    quote/brace ever arrived — every candidate above requires a genuinely complete, balanced
    structure, so a truncated-but-otherwise-real answer got discarded entirely instead of used.
    This walks the text character-by-character (respecting escapes, so a `\\"` inside a string
    never mistakenly counts as the string's end) and closes whatever was still open when the text
    ran out: an unterminated string first, then any still-open `{`/`[` in reverse order. It never
    invents content — only closes structure that's already there — so a genuinely malformed
    response (mismatched brackets, garbage before any `{`) still correctly fails to parse and
    falls through to the real error below, same as before this existed."""
    if "{" not in text:
        return None
    repaired = text[text.index("{"):]

    in_string = False
    escape = False
    stack: list[str] = []
    for ch in repaired:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()

    if not in_string and not stack:
        return None  # already balanced — nothing to repair, let the real candidates handle it

    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")

    # Real, live-found bug (2026-09-21): a smaller model sometimes wraps its answer in an EMPTY
    # code fence (` ```json``` ` with nothing inside). The old code unconditionally overwrote
    # `text` with the fence's (possibly empty) captured group AFTER the emptiness check above had
    # already passed — `json.loads("")` then raised the exact "Expecting value: line 1 column 1
    # (char 0)" seen live, misreported as "could not parse JSON" instead of the real "empty model
    # response". Candidates are now additive (raw text first, then a genuinely non-empty fence
    # capture, then a brace match) instead of destructively replacing `text`.
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence and fence.group(1).strip():
        candidates.append(fence.group(1).strip())
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        candidates.append(brace.group(0))

    # Last resort, tried only after every genuinely-complete candidate above has failed (checked
    # further down) — repairing truncated structure is a real degrade, not the preferred path.
    repair_source = fence.group(1).strip() if fence and fence.group(1).strip() else text
    repaired = _close_truncated_json(repair_source)
    if repaired:
        candidates.append(repaired)

    last_error: Exception | None = None
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            last_error = exc
            # Real, live-found case (2026-09-21): a smaller model sometimes produces one genuinely
            # valid JSON object followed by MORE text (a repeated object, stray commentary) —
            # `json.loads` rejects the whole string as "Extra data" even though the leading object
            # is perfectly valid. `raw_decode` parses just that leading value and reports where it
            # stopped, so trailing junk no longer sinks an otherwise-correct answer.
            try:
                parsed, _ = decoder.raw_decode(candidate.lstrip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    
    # Provide a snippet of the text in the error message so we can see what the model actually returned
    text_snippet = (text[:100] + '...') if len(text) > 100 else text
    raise ValueError(f"could not parse JSON from model response (starts with: {text_snippet!r}): {last_error}")
