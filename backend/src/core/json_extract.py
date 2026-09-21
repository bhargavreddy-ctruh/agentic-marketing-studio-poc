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

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    candidates = [text]
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        candidates.append(brace.group(0))

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            last_error = exc
    raise ValueError(f"could not parse JSON from model response: {last_error}")
