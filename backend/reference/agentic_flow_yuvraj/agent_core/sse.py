"""SSE helpers shared by agentic FastAPI routes."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .events import AgentEvent


def format_sse(payload: dict[str, Any] | AgentEvent) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


async def iter_queue_events(
    *,
    run: Callable[[Callable[[AgentEvent], Awaitable[None]]], Awaitable[None]],
) -> AsyncIterator[AgentEvent]:
    """
    Run an async producer that emits AgentEvents into a queue, and yield them.

    `run` receives an `emit` coroutine. When the producer finishes (or fails),
    a None sentinel closes the iterator. Cancels the producer if the consumer exits early.
    """
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    async def emit(event: AgentEvent) -> None:
        await queue.put(event)

    async def _runner() -> None:
        try:
            await run(emit)
        except Exception as exc:
            await queue.put(
                {
                    "type": "error",
                    "agent": "system",
                    "message": str(exc),
                }
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(_runner())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
