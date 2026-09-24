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

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

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
    specialist_name: str, *, context: str, max_iterations: int = 6, brief: dict | None = None
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

    `brief` — optional; when provided, the real `_recent_chat_history` turns inside it are injected
    as proper conversation-history messages (user/assistant pairs) BEFORE the current context message.
    This is what makes the MASTER_DIRECTIVE's user-override logic actually work: the specialist LLM
    can see "its okay go ahead" as a real prior user turn, not just as a string buried inside a JSON
    dump in the context. Without real message history, the model has no reliable way to detect that
    the user already confirmed proceeding past a guardrail conflict.
    """
    spec = get_specialist(specialist_name)
    system_prompt = spec.load_prompt()
    
    from ...core.events import get_current_guardrails_xml
    guardrails_xml = get_current_guardrails_xml()
    if guardrails_xml:
        system_prompt += f"\n\n{guardrails_xml}\n"

    system_prompt += (
        "\n\n<MASTER_DIRECTIVE>\n"
        "You are creating top-tier, crazy, eye-catching, bold marketing material. "
        "If your instructions are ambiguous or entirely lack context about the subject you should be creating, "
        "FAIL immediately and explicitly ask for clarity. "
        "Never assume, guess, or hallucinate missing details. "
        "IMPORTANT: If the user explicitly asks for something that contradicts the Guardrails, "
        "OR if the user confirms to proceed ('yes', 'go ahead', 'do it') after a guardrail conflict was pointed out in the recent chat history, "
        "the user's instruction TAKES PRECEDENCE for this run. Do NOT fail again for the same conflict in that case, "
        "but follow the user's instruction, ignoring the conflicting guardrail. "
        "To fail gracefully, do NOT return your standard output format. Instead, return EXACTLY this JSON: "
        '{"error": "Your clarifying question or explanation of why you cannot proceed here"}'
        "\n</MASTER_DIRECTIVE>"
    )
    llm = get_llm_provider()
    tool_schemas = [to_openai_tool_schema(get_tool(name)) for name in spec.allowed_tools]

    emit("specialist_started", specialist=specialist_name, tier=spec.tier.name)

    # Build the messages array: real prior chat turns (if brief provided) + current context.
    # This is the mechanism that makes "go ahead" / user confirmations visible to the LLM — they
    # exist as real user/assistant message pairs, not just as text inside a JSON blob.
    from ...core.chat_history import build_history_messages
    if brief is not None:
        messages: list[dict[str, Any]] = build_history_messages(brief, context)
    else:
        messages = [{"role": "user", "content": context}]
    tool_calls: list[ToolCallRecord] = []
    # Real, live-found bug (2026-09-24, per a real user report — `reference_curator` failed twice
    # in a row with "could not parse JSON from model response: Expecting value: line 1 column 1
    # (char 0)"): a model that returns non-JSON prose with no tool call at all (not the same as an
    # EMPTY response, which `extract_json` already reports distinctly) used to kill the whole
    # specialist step immediately — zero retry, anywhere. `reference_curator` specifically prefers
    # the local model (`spec.prefer_local`), whose instruction-following is genuinely weaker than
    # Groq/OpenRouter's free models, making this the specialist most likely to hit it. One bounded
    # corrective retry, same shape as the existing failed-tool-call correction just below (a real
    # system note telling the model exactly what was wrong), instead of raising on the first miss.
    _json_parse_retries_left = 1

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
                    # Real live "thinking" text, per the user's explicit ask (2026-09-21) — a
                    # no-op unless STREAM_LLM_THINKING_ENABLED is on and the provider actually
                    # streams (see base.py's own docstring on this parameter).
                    on_delta=lambda delta: emit("llm_delta", node=specialist_name, text=delta),
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
                            except Exception as exc:  # noqa: BLE001 — real, live-found gap
                                # (2026-09-22): only `ToolNotFound` was ever caught here — any OTHER
                                # real runtime failure inside a tool (a provider error, a malformed
                                # storage_ref pointing at bytes that aren't actually an image, a
                                # network timeout) propagated all the way up uncaught, crashing the
                                # ENTIRE turn with a raw 500 instead of the honest, typed
                                # "this tool call failed" result every other failure mode in this
                                # codebase degrades to. A single misbehaving tool call (e.g. a
                                # hallucinated storage_ref from a genuine model error elsewhere)
                                # must never take down a whole request — the model gets a real
                                # error message back and can decide what to do next, same as any
                                # other failed tool call.
                                log.warning(
                                    "tool_call_raised",
                                    extra={"_extra_tool": tool_name, "_extra_error": str(exc)},
                                )
                                record = ToolCallRecord(
                                    tool_name=tool_name, args=args, ok=False, data={}, error=str(exc)
                                )
                            tool_run.add_outputs({"ok": record.ok, "data": record.data, "error": record.error})
                    tool_calls.append(record)
                    tool_content = {"ok": record.ok, "data": record.data, "error": record.error}
                    if not record.ok:
                        tool_content["_system_note"] = "If a tool fails, do not repeat the exact same call. If you cannot fulfill the request, return your final JSON response immediately."
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(tool_content),
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
                if _json_parse_retries_left > 0:
                    _json_parse_retries_left -= 1
                    log.warning(
                        "specialist_final_json_parse_retry",
                        extra={"_extra_specialist": specialist_name, "_extra_error": str(exc)},
                    )
                    messages.append({"role": "assistant", "content": result.text or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your last reply was not valid JSON and had no tool call — "
                            f"parsing it failed with: {exc}. Return ONLY the required JSON object "
                            "for your final answer (or call a tool if you're not done yet)."
                        ),
                    })
                    continue
                raise SpecialistFailed(specialist_name, f"could not parse final response: {exc}") from exc
                
            if "error" in parsed and len(parsed.keys()) == 1:
                # The model followed the MASTER_DIRECTIVE to fail gracefully
                partial = AgenticStepResult(
                    specialist_name=specialist_name, model=result.model, data=parsed, tool_calls=tool_calls
                )
                exc = SpecialistFailed(specialist_name, parsed["error"])
                exc.partial_result = partial
                raise exc

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


async def run_specialist_with_review(
    specialist_name: str,
    *,
    context: str,
    needs_retry: Callable[[AgenticStepResult], bool],
    reminder: str,
    max_iterations: int = 6,
    first_result: AgenticStepResult | None = None,
    brief: dict | None = None,
) -> AgenticStepResult:
    """Runs a specialist, then a real, bounded "did it actually do what it claims" check — a Lead
    calling this instead of `run_specialist_agentic` directly is genuinely reviewing its sub-agent's
    work rather than trusting a plain input->output call (Tasks.md #3, 2026-09-22).

    Generalizes a corrective-retry pattern proven twice already this session on two unrelated real
    bugs — Illustrator's final JSON implying an image was made when `base_image_generator` was
    never actually called, and Sound Designer recommending a voiceover it never actually synthesized
    via `text_to_speech` — instead of leaving every future Lead to hand-roll the same
    check-then-retry logic again. `needs_retry` is a predicate, not a fixed tool list, because the
    real check differs per case: Illustrator's is unconditional (producing SOME image is always
    required), Sound Designer's is conditional (only when it claims "voiceover" specifically) — a
    fixed "was any of these tools called" shape can't express both correctly.

    Deliberately NOT a semantic/quality judgment call, and deliberately not applied after every
    single specialist everywhere — that territory already belongs to `compliance_gate.py`, which
    does real final-output critique-and-remediation against rendered pixels/format (Tasks.md #3's
    own scope note: don't duplicate a guardrail that already exists elsewhere). This is narrower
    and cheaper: did the specialist's own claim match what it actually did, structurally — exactly
    the two real bugs found so far, generalized, not a broader creative-quality review.

    `first_result` lets a caller that already ran the specialist's first attempt itself (e.g. as
    part of an `asyncio.gather` alongside other, genuinely independent specialists — motion_lead.py's
    Sound Designer runs concurrently with the video path, so its first call can't be made FROM
    inside this function) still route the review/retry decision through this one shared place,
    rather than reimplementing the check-and-log-and-retry logic a second time."""
    result = first_result or await run_specialist_agentic(
        specialist_name, context=context, max_iterations=max_iterations, brief=brief
    )
    if not needs_retry(result):
        return result
    log.warning("specialist_review_retry", extra={"_extra_specialist": specialist_name})
    # Real Node Mode visibility (2026-09-22, per the user's own ask to reflect Task 3's review
    # capability on screen): previously only logged, never emitted as a real SSE event, so a
    # genuine Lead-catches-and-corrects-a-specialist moment was invisible in the live view — the
    # retry's second `specialist_started`/`_completed` pair just silently overwrote the same
    # card, with no sign a review/correction ever happened.
    emit("specialist_review_retry", specialist=specialist_name)
    return await run_specialist_agentic(
        specialist_name, context=f"{context}\n\n{reminder}", max_iterations=max_iterations, brief=brief
    )


async def run_concurrent_specialists(
    branches: dict[str, Coroutine[Any, Any, AgenticStepResult]],
    *,
    critical: set[str] = frozenset(),
) -> dict[str, AgenticStepResult]:
    """Runs several genuinely independent specialist calls concurrently (Tasks.md #4) — the
    generalized form of the pattern `motion_lead.py` proved first (Sound Designer / Overlay Artist
    alongside the video path) and a real bug in it exposed on independent review: plain
    `asyncio.gather` with no `return_exceptions=True` doesn't cancel siblings on a failure, it only
    propagates the first exception — so one branch failing used to silently discard another
    branch's already-computed (sometimes already-PAID-for) real result along with it.

    This function only ever changes HOW failure is handled, never WHICH specialists get called
    concurrently — that remains a static, per-Lead, code-level decision (Tasks.md #4's own open
    question, resolved by a real dependency audit across every Lead, not by having an LLM infer
    independence at runtime: Visual Design Lead and Scene Lead are both genuinely serial — each
    step's real output feeds the next step's real input, or edits the very same asset a prior step
    just produced — and forcing concurrency there would be a real correctness risk, not an
    optimization. Only Narrative Lead and Motion Lead have branches with no such real dependency).

    A `critical` branch's real exception re-raises (there's nothing meaningful to return without
    it — matches every other "let a genuine failure surface" pattern in this codebase). Every
    other branch degrades to an empty `AgenticStepResult` (same shape Illustrator/Sound Designer's
    own fallbacks already build by hand) with a logged warning, instead of losing a sibling's real
    result to an unrelated branch's failure."""
    names = list(branches.keys())
    results = await asyncio.gather(*branches.values(), return_exceptions=True)
    out: dict[str, AgenticStepResult] = {}
    for name, result in zip(names, results):
        if isinstance(result, BaseException):
            if name in critical:
                raise result
            log.warning(
                "concurrent_specialist_degraded",
                extra={"_extra_specialist": name, "_extra_error": str(result)},
            )
            out[name] = AgenticStepResult(specialist_name=name, model="", data={}, tool_calls=[])
        else:
            out[name] = result
    return out
