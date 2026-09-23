"""
Best-effort JSON extraction from LLM text output — free-tier models are more likely than a
frontier model to wrap JSON in prose or a code fence. Ported pattern from the existing
agentic_flow codebase's flow_builder `_extract_json` (Rules.md section 6).
"""
from __future__ import annotations

import json
import re
from typing import Any


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
    raise ValueError(f"could not parse JSON from model response: {last_error}")
