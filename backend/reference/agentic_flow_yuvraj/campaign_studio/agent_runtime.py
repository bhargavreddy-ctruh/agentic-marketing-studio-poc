"""
agent_runtime.py — Re-exports shared agent_core runtime (backward compatible).
"""
from __future__ import annotations

from ..agent_core.runtime import (  # noqa: F401
    EmitFn,
    TerminalToolResult,
    ToolHandler,
    current_agent,
    run_agent,
    terminal,
)

__all__ = [
    "EmitFn",
    "TerminalToolResult",
    "ToolHandler",
    "current_agent",
    "run_agent",
    "terminal",
]
