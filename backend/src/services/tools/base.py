"""
The Tool protocol every tool implements — Rules.md section 1 (Open/Closed via the registry).

A new tool is one new file implementing this protocol, self-registered via @register_tool.
Nothing else — not the orchestrator, not a Lead, not another specialist — ever changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any]
    error: str | None = None


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    async def run(self, args: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult: ...
