"""
runtime.py — Generic multi-turn tool-use agent loop on Anthropic Claude.
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from .config import AGENT_MAX_TURNS
from .events import AgentEvent
from .llm import (
    AnthropicClient,
    LLMError,
    get_llm_client,
    tool_result_block,
)
from .logger import get_logger

log = get_logger(__name__)

EmitFn = Callable[[AgentEvent], Awaitable[None]]
ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# Name of the agent currently running in this task, so a nested run_agent can
# record who delegated to it. A ContextVar rather than a parameter because
# delegation happens inside tool handlers across six services — threading a
# parent argument through every call site would touch all of them, and
# contextvars already propagate correctly across await and per-task copies.
_current_agent: ContextVar[str | None] = ContextVar("current_agent", default=None)

# Keys whose values are image payloads or other bulk. Never put these in an
# event: view_product_images alone returns four full base64 image blocks.
_BULKY_KEYS = {
    "imagebase64", "image_base64", "data", "image", "images", "__images__",
    "person_image", "garment_image", "b64", "base64", "bytes", "content",
}

_MAX_STR = 200
_MAX_ITEMS = 6
_MAX_DEPTH = 4
_MAX_PREVIEW_CHARS = 2000


class TerminalToolResult(Exception):
    """Raised by a terminal tool handler to end the agent loop with a result."""

    def __init__(self, tool_name: str, payload: dict[str, Any]):
        super().__init__(tool_name)
        self.tool_name = tool_name
        self.payload = payload


def current_agent() -> str | None:
    """The agent running in this task, if any. Exposed for tool handlers."""
    return _current_agent.get()


def _summarize(value: Any, depth: int = 0) -> Any:
    """
    Recursively shrink a value to something safe to put on an event.

    Tool results vary in shape across services and can contain base64 image
    blocks nested several levels down, so this is deliberately structural
    rather than a fixed key list.
    """
    if depth >= _MAX_DEPTH:
        return "[deep]"

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["…"] = f"{len(value) - _MAX_ITEMS} more key(s)"
                break
            if str(k).lower() in _BULKY_KEYS:
                if isinstance(v, (list, tuple)):
                    out[k] = f"[{len(v)} item(s) omitted]"
                elif isinstance(v, str):
                    out[k] = f"[{len(v)} chars omitted]"
                else:
                    out[k] = "[omitted]"
            else:
                out[k] = _summarize(v, depth + 1)
        return out

    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_ITEMS:
            return [_summarize(v, depth + 1) for v in value[:_MAX_ITEMS]] + [
                f"…{len(value) - _MAX_ITEMS} more"
            ]
        return [_summarize(v, depth + 1) for v in value]

    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…"

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return f"[{type(value).__name__}]"


# Reasoning blocks. Valid inside an assistant turn, never as its last block.
_THINKING_BLOCKS = {"thinking", "redacted_thinking"}


def _sendable(content: list[Any] | None) -> list[Any]:
    """
    An assistant turn as it can legally be sent back.

    A response truncated mid-thought — `max_tokens` reached while the model was
    still reasoning — ends with a `thinking` block, and the API refuses an
    assistant message shaped that way. Echoing it back poisons the conversation:
    every later turn fails with the same 400, the agent dies, and a caller that
    catches agent failures records the check as "did not run" rather than as
    broken. That is how a Guardian pass silently went missing.

    Trailing reasoning is dropped, because a truncated thought has no tool call
    for it to belong to and nothing downstream needs it. Reasoning that precedes
    real output is left alone — it is required to stay with the tool call it
    reasoned about.
    """
    blocks = [b for b in (content or []) if isinstance(b, dict)]
    while blocks and blocks[-1].get("type") in _THINKING_BLOCKS:
        blocks.pop()
    return blocks


def _preview_result(result: Any) -> Any:
    """Summarize a tool result for an event, with a hard ceiling on size."""
    summary = _summarize(result)
    try:
        encoded = json.dumps(summary, default=str)
    except Exception:  # noqa: BLE001 — a preview must never break the run
        return "[unserialisable]"
    if len(encoded) > _MAX_PREVIEW_CHARS:
        return {"truncated": True, "preview": encoded[:_MAX_PREVIEW_CHARS] + "…"}
    return summary


async def run_agent(
    *,
    name: str,
    system_prompt: str,
    initial_content: list[dict[str, Any]] | str,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, ToolHandler],
    emit: EmitFn,
    client: AnthropicClient | None = None,
    max_turns: int | None = None,
    max_tokens: int = 4096,
    terminal_tools: set[str] | None = None,
    allow_text_only_finish: bool = False,
) -> dict[str, Any]:
    """
    Run a tool-using agent until a terminal tool is called or max turns reached.

    Returns the payload from the terminal tool, or {"reply": text} when
    allow_text_only_finish is True and the model ends with text only.

    Emits a trace as it goes: agent_started, turn_started, the tool call and
    its tool_result or tool_failed, and a finish message. Every event carries
    `data.parent`, so a consumer can render delegation as a tree.

    Args:
        max_tokens: Token budget per LLM call. Agents with large tool outputs
            (Merchandiser, Director) should use 8192; focused agents (Layout,
            QA) can use 4096.
    """
    llm = client or get_llm_client()
    turns = max_turns or AGENT_MAX_TURNS
    terminal = terminal_tools or set()

    parent = _current_agent.get()
    token = _current_agent.set(name)
    agent_started_at = time.perf_counter()

    def _elapsed_ms(since: float) -> float:
        # 1dp rather than int: most submit_* handlers are sub-millisecond, and a
        # column of "0ms" reads as a broken timer rather than a fast call.
        return round((time.perf_counter() - since) * 1000, 1)

    try:
        if isinstance(initial_content, str):
            user_content: list[dict[str, Any]] | str = initial_content
        else:
            user_content = initial_content

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_content},
        ]

        await emit(
            {
                "type": "agent_started",
                "agent": name,
                "message": f"{name} is thinking",
                "data": {"parent": parent, "maxTurns": turns},
            }
        )

        last_text = ""

        for turn in range(turns):
            log.info("agent=%s turn=%d/%d", name, turn + 1, turns)
            await emit(
                {
                    "type": "turn_started",
                    "agent": name,
                    "message": f"{name} turn {turn + 1}/{turns}",
                    "data": {"turn": turn + 1, "maxTurns": turns, "parent": parent},
                }
            )

            response = await llm.chat(
                system=system_prompt,
                messages=messages,
                tools=tools or None,
                max_tokens=max_tokens,
            )

            sendable = _sendable(response.content)
            if sendable:
                messages.append({"role": "assistant", "content": sendable})

            if response.text.strip():
                last_text = response.text.strip()
                await emit(
                    {
                        "type": "agent_message",
                        "agent": name,
                        "message": last_text[:800],
                        "data": {"parent": parent, "turn": turn + 1},
                    }
                )

            tool_uses = response.tool_uses
            if not tool_uses:
                if response.stop_reason == "end_turn":
                    if allow_text_only_finish and last_text:
                        await emit(
                            {
                                "type": "agent_message",
                                "agent": name,
                                "message": f"{name} finished",
                                "data": {
                                    "parent": parent,
                                    "turns": turn + 1,
                                    "durationMs": _elapsed_ms(agent_started_at),
                                },
                            }
                        )
                        return {"reply": last_text}
                    if turn < turns - 1:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "You must call one of your available tools to proceed. "
                                    "Do not reply with text only."
                                ),
                            }
                        )
                        continue
                    raise LLMError(
                        f"Agent '{name}' ended without calling a terminal tool",
                        502,
                    )

                # Stopped for a reason other than finishing — max_tokens, most
                # often, with the turn truncated mid-thought. Say so and ask for
                # something shorter: repeating the request repeats the truncation.
                if response.stop_reason == "max_tokens":
                    log.warning(
                        "agent=%s turn=%d hit max_tokens before producing a tool call",
                        name,
                        turn + 1,
                    )
                    if turn < turns - 1:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your last reply was cut off before you called a "
                                    "tool. Reason briefly and call the tool now."
                                ),
                            }
                        )
                        continue
                    raise LLMError(
                        f"Agent '{name}' kept running out of tokens before calling a tool",
                        502,
                    )
                continue

            tool_results: list[dict[str, Any]] = []
            terminal_payload: dict[str, Any] | None = None
            terminal_name: str | None = None

            for tu in tool_uses:
                tool_name = str(tu.get("name") or "")
                tool_id = str(tu.get("id") or "")
                raw_input = tu.get("input") or {}
                if isinstance(raw_input, str):
                    try:
                        args = json.loads(raw_input)
                    except json.JSONDecodeError:
                        args = {}
                elif isinstance(raw_input, dict):
                    args = raw_input
                else:
                    args = {}

                await emit(
                    {
                        "type": "agent_message",
                        "agent": name,
                        "message": f"Calling tool `{tool_name}`",
                        "data": {
                            "tool": tool_name,
                            "argsPreview": _preview_args(args),
                            "toolUseId": tool_id,
                            "parent": parent,
                            "turn": turn + 1,
                        },
                    }
                )

                handler = tool_handlers.get(tool_name)
                if not handler:
                    await emit(
                        {
                            "type": "tool_failed",
                            "agent": name,
                            "message": f"`{tool_name}` is not a tool this agent has",
                            "data": {
                                "tool": tool_name,
                                "toolUseId": tool_id,
                                "ok": False,
                                "error": f"Unknown tool: {tool_name}",
                                "parent": parent,
                                "turn": turn + 1,
                            },
                        }
                    )
                    tool_results.append(
                        tool_result_block(
                            tool_id,
                            {"error": f"Unknown tool: {tool_name}"},
                            is_error=True,
                        )
                    )
                    continue

                tool_started_at = time.perf_counter()
                try:
                    result = await handler(args)
                    is_terminal = isinstance(result, dict) and result.get("__terminal__")
                    if is_terminal:
                        terminal_name = tool_name
                        terminal_payload = result.get("payload") or {}
                        tool_results.append(
                            tool_result_block(tool_id, {"ok": True, "submitted": True})
                        )
                    else:
                        tool_results.append(tool_result_block(tool_id, result))

                    # A handler that returns {"error": ...} has failed even though it
                    # did not raise — validation rejections come back this way, and the
                    # model then retries. Reporting those as ok made the trace lie about
                    # what happened.
                    handler_error = (
                        result.get("error") if isinstance(result, dict) else None
                    )
                    await emit(
                        {
                            "type": "tool_result" if not handler_error else "tool_failed",
                            "agent": name,
                            "message": (
                                f"`{tool_name}` returned"
                                if not handler_error
                                else f"`{tool_name}` rejected: {str(handler_error)[:200]}"
                            ),
                            "data": {
                                "tool": tool_name,
                                "toolUseId": tool_id,
                                "ok": not handler_error,
                                "terminal": bool(is_terminal),
                                "durationMs": _elapsed_ms(tool_started_at),
                                **({"error": str(handler_error)[:500]} if handler_error else {}),
                                "resultPreview": _preview_result(
                                    terminal_payload if is_terminal else result
                                ),
                                "parent": parent,
                                "turn": turn + 1,
                            },
                        }
                    )
                except TerminalToolResult as term:
                    terminal_name = term.tool_name
                    terminal_payload = term.payload
                    tool_results.append(
                        tool_result_block(tool_id, {"ok": True, "submitted": True})
                    )
                    await emit(
                        {
                            "type": "tool_result",
                            "agent": name,
                            "message": f"`{tool_name}` returned",
                            "data": {
                                "tool": tool_name,
                                "toolUseId": tool_id,
                                "ok": True,
                                "terminal": True,
                                "durationMs": _elapsed_ms(tool_started_at),
                                "resultPreview": _preview_result(term.payload),
                                "parent": parent,
                                "turn": turn + 1,
                            },
                        }
                    )
                except Exception as exc:
                    # Previously logged only, so a failing tool was invisible to
                    # anything watching the stream.
                    log.warning("tool %s failed in agent %s: %s", tool_name, name, exc)
                    tool_results.append(
                        tool_result_block(tool_id, {"error": str(exc)}, is_error=True)
                    )
                    await emit(
                        {
                            "type": "tool_failed",
                            "agent": name,
                            "message": f"`{tool_name}` failed: {str(exc)[:200]}",
                            "data": {
                                "tool": tool_name,
                                "toolUseId": tool_id,
                                "ok": False,
                                "error": str(exc)[:500],
                                "errorType": type(exc).__name__,
                                "durationMs": _elapsed_ms(tool_started_at),
                                "parent": parent,
                                "turn": turn + 1,
                            },
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

            if terminal_payload is not None:
                if not terminal or (terminal_name in terminal):
                    await emit(
                        {
                            "type": "agent_message",
                            "agent": name,
                            "message": f"{name} finished via `{terminal_name}`",
                            "data": {
                                "parent": parent,
                                "terminalTool": terminal_name,
                                "turns": turn + 1,
                                "durationMs": _elapsed_ms(agent_started_at),
                            },
                        }
                    )
                    return terminal_payload

        raise LLMError(f"Agent '{name}' exceeded max turns ({turns})", 502)
    finally:
        _current_agent.reset(token)


def _preview_args(args: dict[str, Any]) -> dict[str, Any]:
    """Strip bulky fields from args for SSE timeline."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k.lower() in {"imagebase64", "data", "image", "person_image", "garment_image"}:
            out[k] = "[omitted]"
        elif isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "…"
        else:
            out[k] = v
    return out


def terminal(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload so the runtime ends the agent loop."""
    return {"__terminal__": True, "payload": payload}
