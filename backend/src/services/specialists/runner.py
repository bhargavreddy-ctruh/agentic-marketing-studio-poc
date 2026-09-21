"""
The shared "run one specialist" helper. Every Lead executor uses this instead of hand-rolling its
own LLM-calling code per specialist — Rules.md section 1 (DRY).

A REAL agentic tool-use loop (Memory.md, Phase 2 — the move away from the earlier "LLM decides in
text, code calls the tool" design): a specialist with `allowed_tools` is offered those tools as
real OpenAI-compatible function-calling schemas. The LLM itself decides whether to call zero, one,
or several of them, in whatever order it chooses, inspecting each tool's real result before
deciding its next move — genuinely dynamic, not a fixed sequence Python code enforces. Verified
live against both OpenRouter and Groq's free models (Memory.md, Phase 2) before this replaced the
earlier one-shot-JSON design; every free model this project pins already lists `tools` as a
supported parameter.

Bounded by max_iterations so a model that never stops calling tools can't loop forever — that cap
itself surfaces as a typed SpecialistFailed, never a silent hang.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ...core.events import emit
from ...core.exceptions import ProviderUnavailable, SpecialistFailed, ToolNotFound
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import trace
from ..tools.registry import get_tool, to_openai_tool_schema
from .registry import get_specialist

log = get_logger(__name__)


@dataclass
class ToolCallRecord:
    """One real tool invocation a specialist's LLM genuinely chose to make, with its real result —
    kept so the calling Lead executor can find what it needs (e.g. "the storage_ref this step
    produced") without re-deriving it, and so it's visible in canvas-element metadata for
    transparency about what actually happened during this step, not just what was asked for."""

    tool_name: str
    args: dict[str, Any]
    ok: bool
    data: dict[str, Any]
    error: str | None = None


@dataclass
class AgenticStepResult:
    """A typed envelope around one specialist's full agentic turn — its final parsed JSON
    decision plus every real tool call it made along the way (Rules.md section 2: no bare dict
    crossing a layer boundary)."""

    specialist_name: str
    model: str
    data: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def latest_result(self, *tool_names: str) -> dict[str, Any] | None:
        """The most recent successful call's result data among the given tool names — e.g. "the
        final image this step actually produced," whichever of its allowed image tools made it."""
        for call in reversed(self.tool_calls):
            if call.ok and call.tool_name in tool_names:
                return call.data
        return None

    def latest_call(self, *tool_names: str) -> ToolCallRecord | None:
        """Same as latest_result(), but returns the whole record — needed when a caller wants the
        real ARGUMENTS a tool was actually invoked with (e.g. the real aspect_ratio actually sent
        to base_image_generator), not just its result, since the LLM's own final-JSON text field
        can in principle drift from what it really passed to the tool."""
        for call in reversed(self.tool_calls):
            if call.ok and call.tool_name in tool_names:
                return call
        return None


async def run_specialist_agentic(
    specialist_name: str, *, context: str, max_iterations: int = 6
) -> AgenticStepResult:
    """
    Runs one specialist's full agentic turn: offers its `allowed_tools` as real function-calling
    tools, executes whichever ones the model genuinely chooses (in whatever order/count it picks),
    feeds each real result back, and loops until the model stops calling tools and returns its
    final JSON decision.

    Every failure mode — provider unavailable, a tool call to something not in allowed_tools,
    malformed final JSON, exceeding max_iterations — surfaces as SpecialistFailed specifically, so
    a Lead executor only ever needs to catch one exception type (Rules.md section 1: DRY, section
    4: typed errors).
    """
    spec = get_specialist(specialist_name)
    system_prompt = spec.load_prompt()
    llm = get_llm_provider()
    tool_schemas = [to_openai_tool_schema(get_tool(name)) for name in spec.allowed_tools]

    emit("specialist_started", specialist=specialist_name, tier=spec.tier.name)

    messages: list[dict[str, Any]] = [{"role": "user", "content": context}]
    tool_calls: list[ToolCallRecord] = []

    # A dynamic per-call trace name (`trace()`, not `@traceable`) — this one function runs every
    # specialist, so a static decorator name would make all of them look identical in LangSmith. A
    # real, live-found gap (2026-09-21): most graph-level nodes were already traced, but individual
    # specialists and the tool calls underneath them were invisible, collapsed into whichever Lead
    # node called them.
    async with trace(
        name=f"specialist:{specialist_name}", run_type="chain", inputs={"context": context}
    ) as specialist_run:
        for iteration in range(max_iterations):
            try:
                result = await llm.complete(
                    tier=spec.tier,
                    system=system_prompt,
                    messages=messages,
                    tools=tool_schemas or None,
                    # 1024 was too tight in practice (Memory.md, Phase 1) — same reasoning-overhead
                    # finding as ideation_service.py, and again with Groq's gpt-oss models.
                    max_tokens=2048,
                    # Per-specialist opt-in (registry.py's SpecialistSpec.prefer_local, default
                    # False) — a real, live-found regression (2026-09-21) showed the local model
                    # isn't safe to assume for every specialist just because one (Reference
                    # Curator) tested fine.
                    prefer_local=spec.prefer_local,
                )
            except ProviderUnavailable as exc:
                emit("specialist_failed", specialist=specialist_name, reason=exc.message)
                raise SpecialistFailed(specialist_name, exc.message) from exc

            if result.tool_calls:
                messages.append(
                    {"role": "assistant", "content": result.text or None, "tool_calls": result.tool_calls}
                )
                for call in result.tool_calls:
                    fn = call.get("function", {})
                    tool_name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    if tool_name not in spec.allowed_tools:
                        record = ToolCallRecord(
                            tool_name=tool_name, args=args, ok=False, data={},
                            error=f"'{tool_name}' is not in {specialist_name}'s allowed_tools",
                        )
                    else:
                        # Its own trace span, nested under this specialist's — real per-tool
                        # visibility (name, real args, real result), not just a line in the
                        # specialist's own log.
                        async with trace(name=f"tool:{tool_name}", run_type="tool", inputs=args) as tool_run:
                            try:
                                tool_result = await get_tool(tool_name).run(args)
                                record = ToolCallRecord(
                                    tool_name=tool_name, args=args, ok=tool_result.ok,
                                    data=tool_result.data, error=tool_result.error,
                                )
                            except ToolNotFound as exc:
                                record = ToolCallRecord(
                                    tool_name=tool_name, args=args, ok=False, data={}, error=str(exc)
                                )
                            tool_run.add_outputs({"ok": record.ok, "data": record.data, "error": record.error})
                    tool_calls.append(record)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps({"ok": record.ok, "data": record.data, "error": record.error}),
                    })
                    log.info(
                        "specialist_tool_call",
                        extra={
                            "_extra_specialist": specialist_name,
                            "_extra_tool": tool_name,
                            "_extra_ok": record.ok,
                            "_extra_iteration": iteration + 1,
                        },
                    )
                    emit("tool_call", specialist=specialist_name, tool=tool_name, ok=record.ok)
                continue  # let the model see the real tool results before deciding what's next

            # No tool calls this turn -> the model considers itself done; parse its final decision.
            try:
                parsed = extract_json(result.text)
            except ValueError as exc:
                raise SpecialistFailed(specialist_name, f"could not parse final response: {exc}") from exc

            log.info(
                "specialist_step_ok",
                extra={
                    "_extra_specialist": specialist_name,
                    "_extra_tier": spec.tier.name,
                    "_extra_model": result.model,
                    "_extra_iterations": iteration + 1,
                    "_extra_tool_calls": len(tool_calls),
                },
            )
            emit("specialist_completed", specialist=specialist_name, tool_call_count=len(tool_calls))
            specialist_run.add_outputs({"data": parsed, "tool_call_count": len(tool_calls)})
            return AgenticStepResult(
                specialist_name=specialist_name, model=result.model, data=parsed, tool_calls=tool_calls
            )

        emit("specialist_failed", specialist=specialist_name, reason="max_iterations_exceeded")
        raise SpecialistFailed(
            specialist_name, f"did not produce a final answer after {max_iterations} tool-calling iterations"
        )
