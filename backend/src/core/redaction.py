"""
Redact bulky/sensitive fields before they hit a log, a trace, or the tool_call_logs table.

Direct port of the existing agentic_flow codebase's `_preview_args()` pattern (Rules.md section 6)
— now more important than before, not less: LangSmith traces should never carry raw base64 image
data, both for trace-payload size and for not leaking content into a third-party tool.
"""
from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = {"imagebase64", "data", "image", "image_base64", "person_image", "garment_image"}
_MAX_STRING_LEN = 200


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in _SENSITIVE_KEYS:
            out[key] = "[omitted]"
        elif isinstance(value, str) and len(value) > _MAX_STRING_LEN:
            out[key] = value[:_MAX_STRING_LEN] + "…"
        else:
            out[key] = value
    return out
