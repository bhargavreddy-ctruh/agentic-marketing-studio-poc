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

import ast
import asyncio
import json
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from ...core.events import emit
from ...core.exceptions import ProviderUnavailable, SpecialistFailed, ToolNotFound
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...core.redaction import redact_args
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import trace
from ..tools.registry import get_tool, to_openai_tool_schema
from .registry import get_specialist

log = get_logger(__name__)

# Tools whose `aspect_ratio` arg gets deterministically enforced from the real user message
# instead of trusted from the model's own tool-call arg — see the call site below.
_ASPECT_RATIO_ENFORCED_TOOLS = frozenset({"base_image_generator", "image_editor", "base_video_generator"})


def _extract_balanced_call_args(text: str, open_paren_idx: int) -> str | None:
    """Scans forward from an opening '(' to find its matching ')', respecting quoted strings (so
    a ')' inside a string literal doesn't end the scan early) and backslash-escaped characters
    within them. Returns the full '(...)' substring (inclusive), or None if the parens never
    balance (e.g. the model's output was truncated mid-call) — never guessed at."""
    depth = 0
    in_quote: str | None = None
    i = open_paren_idx
    n = len(text)
    while i < n:
        c = text[i]
        if in_quote:
            if c == "\\":
                i += 2
                continue
            if c == in_quote:
                in_quote = None
        else:
            if c in ("'", '"'):
                in_quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return text[open_paren_idx : i + 1]
        i += 1
    return None


def _recover_pseudo_tool_call(text: str, allowed_tools: tuple[str, ...]) -> tuple[str, dict[str, Any]] | None:
    """
    Real, live-found recovery (2026-09-26, live-reproduced against `illustrator`): a provider with
    no native tool-calling (Replicate's Gemini fallback, confirmed via its own schema — no
    `tools`/`functions` input field exists on it) — or a weaker Groq fallback model under provider
    stress — sometimes expresses a tool call as a Python-style function-call expression instead of
    a real `tool_calls` structure, most commonly Gemini's own `{"tool_code":
    "print(base_image_generator(prompt='...', ...))"}` habit. Discarding that text and failing
    outright throws away a clearly-expressed, recoverable intent. This recognizes and safely
    parses ONLY the call expression itself via `ast.parse(..., mode="eval")` — never `eval()`/
    `exec()`, no code execution ever happens — and only accepts plain constant keyword arguments
    (strings, numbers, booleans, None); anything else (nested calls, comprehensions, arbitrary
    expressions, positional args) is rejected as unrecoverable rather than guessed at. Deliberately
    provider-agnostic (operates on the final response text, not tied to any one provider) so the
    same recovery covers Groq falling into this same pattern too, not just Replicate.

    Tries two shapes, in order:
    1. Real JSON `{"tool_call": {"name": "...", "arguments": {...}}}` — in case the model DID
       follow the prompt instruction asking for this exact shape (see `replicate_llm.py`'s own
       `if tools:` block).
    2. A bare/embedded function-call expression `tool_name(kwarg=value, ...)` found anywhere in
       the text (e.g. inside a "tool_code" string) — the fallback for when it didn't.

    Returns (tool_name, arguments) on a safe, confident recovery, else None (the caller falls
    through to the existing bounded-retry-then-SpecialistFailed path, unchanged)."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            call = parsed.get("tool_call")
            if isinstance(call, dict):
                name = call.get("name")
                args = call.get("arguments")
                if isinstance(name, str) and name in allowed_tools and isinstance(args, dict):
                    return name, args
    except (json.JSONDecodeError, TypeError):
        pass

    for name in allowed_tools:
        match = re.search(rf"\b{re.escape(name)}\s*\(", text)
        if not match:
            continue
        call_str = _extract_balanced_call_args(text, match.end() - 1)
        if call_str is None:
            continue  # unbalanced parens (truncated output) — not safely recoverable
        try:
            tree = ast.parse(f"{name}{call_str}", mode="eval")
        except SyntaxError:
            continue
        call_node = tree.body
        if not isinstance(call_node, ast.Call):
            continue
        if not (isinstance(call_node.func, ast.Name) and call_node.func.id == name):
            continue
        if call_node.args:
            continue  # positional args aren't safely attributable to a schema field — reject
        args = {}
        safe = True
        for kw in call_node.keywords:
            if kw.arg is None or not isinstance(kw.value, ast.Constant):
                safe = False
                break
            args[kw.arg] = kw.value.value
        if safe:
            return name, args
    return None


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
    specialist_name: str, *, context: str | list[dict[str, Any]], max_iterations: int = 6, brief: dict | None = None
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
        "CRITICAL RULE 1 — Referenced elements: If the context mentions a referenced element "
        "(a storage_ref, 'referenced image', 'the referenced mobile', or any element the user pointed to), "
        "that element IS the product/subject. NEVER ask which product or model to use in that case — just use it. "
        "CRITICAL RULE 2 — Product nouns: If the user says 'the mobile', 'the sneaker', 'the product', "
        "or any noun referring to a product, treat it as sufficient — generate using that subject. "
        "Only fail for clarification if the request is COMPLETELY void of any subject (e.g. just 'make something nice' with zero context). "
        "CRITICAL RULE 3 — Product color vs. brand color: A brand's approved-colors guardrail governs "
        "brand-OWNED visual elements — backgrounds, accents, brand graphics, packaging/logo treatments — "
        "NEVER the literal, real-world color of the product itself. If the user asks for a product in a "
        "specific real color (e.g. 'a red sneaker'), that is a genuine product fact, not a brand-identity "
        "choice — depict it in that color. Do NOT treat this as a guardrail conflict, do NOT ask whether to "
        "change the brand's color guideline over it, and do NOT refuse or pause for clarification on this basis alone. "
        "CRITICAL RULE 4 — User overrides: If the user explicitly asks for something that contradicts the Guardrails, "
        "OR if the user confirms to proceed ('yes', 'go ahead', 'do it', 'its okay', 'go ahead with it') "
        "after a guardrail conflict was pointed out in the recent chat history, "
        "OR if the user resubmits the SAME or a substantially similar request again after a guardrail "
        "conflict was already pointed out for it in the recent chat history (a real, live-found gap: "
        "repeating the identical request is itself a clear signal the user wants it done as originally "
        "asked, not a request to be asked the same clarifying question a second time), "
        "the user's instruction TAKES PRECEDENCE for this run. Do NOT fail again for the same conflict in that case, "
        "but follow the user's instruction, ignoring the conflicting guardrail. "
        "To handle ambiguity, ask a clarifying question to the user (e.g., \"Could you specify the product or model you want?\") and wait for their response. Then retry the specialist with the new information. Do NOT return a JSON error or abort execution.\n"
        "</MASTER_DIRECTIVE>"
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
    # specialist step immediately — zero retry, anywhere. One bounded corrective retry, same shape
    # as the existing failed-tool-call correction just below (a real system note telling the model
    # exactly what was wrong), instead of raising on the first miss.
    _json_parse_retries_left = 1
    # Code-enforced output contract (2026-09-26, decomposition-quality investigation): every
    # specialist's own prompt already declares a strict <output_format>, but nothing checked the
    # model actually returned those keys — same bounded-retry shape as the JSON-parse-failure
    # case just above, not a new retry system.
    _missing_fields_retries_left = 1

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
                    # Real live "thinking" text, per the user's explicit ask (2026-09-21) — a
                    # no-op unless STREAM_LLM_THINKING_ENABLED is on and the provider actually
                    # streams (see base.py's own docstring on this parameter).
                    on_delta=lambda delta: emit("llm_delta", node=specialist_name, text=delta),
                )
            except ProviderUnavailable as exc:
                emit("specialist_failed", specialist=specialist_name, reason=exc.message)
                raise SpecialistFailed(specialist_name, exc.message) from exc

            effective_tool_calls = result.tool_calls
            if not effective_tool_calls and result.text:
                recovered = _recover_pseudo_tool_call(result.text, spec.allowed_tools)
                if recovered is not None:
                    rec_name, rec_args = recovered
                    log.warning(
                        "recovered_pseudo_tool_call",
                        extra={"_extra_specialist": specialist_name, "_extra_tool": rec_name},
                    )
                    emit("tool_call_recovered", specialist=specialist_name, tool=rec_name)
                    effective_tool_calls = [
                        {"id": f"recovered-{iteration}", "function": {"name": rec_name, "arguments": json.dumps(rec_args)}}
                    ]

            if effective_tool_calls:
                messages.append(
                    {"role": "assistant", "content": result.text or None, "tool_calls": effective_tool_calls}
                )
                for call in effective_tool_calls:
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
                        # Real, live-found bug (2026-09-26): a deterministic aspect-ratio detector
                        # already existed (`infer_aspect_ratio_from_text`) and correctly matched an
                        # explicit "9:16" in a real user request — but it was only ever wired in as
                        # a soft PROMPT HINT the model could (and did) ignore; the real generation
                        # still went out as the model's own default. Enforced here instead, at the
                        # one shared tool-execution point every specialist's tool call passes
                        # through (same choke point the stale-photo-reference fix uses) — covers
                        # image AND video AND every route (dynamic executor, visual_design_lead,
                        # motion_lead) at once, not a hint repeated at three separate call sites.
                        if tool_name in _ASPECT_RATIO_ENFORCED_TOOLS and brief:
                            from ..leads.base import infer_aspect_ratio_from_text
                            detected_ratio = infer_aspect_ratio_from_text(
                                brief.get("_current_turn_message") or brief.get("idea") or ""
                            )
                            if detected_ratio and args.get("aspect_ratio") != detected_ratio:
                                args["aspect_ratio"] = detected_ratio
                        # Its own trace span, nested under this specialist's — real per-tool
                        # visibility (name, real args, real result), not just a line in the
                        # specialist's own log.
                        async with trace(name=f"tool:{tool_name}", run_type="tool", inputs=args) as tool_run:
                            try:
                                tool_context: dict | None = None
                                if brief:
                                    tool_context = {
                                        k: v for k, v in {
                                            "user_id": brief.get("user_id"),
                                            # product_photo_storage_ref is the safety-net fallback:
                                            # base_image_generator checks ctx first if the LLM
                                            # didn't explicitly pass reference_storage_ref as an arg.
                                            "product_photo_storage_ref": brief.get("product_photo_storage_ref"),
                                            "reference_storage_ref": next(
                                                (
                                                    el["storage_ref"] for el in brief.get("referenced_elements_context", [])
                                                    if el.get("element_type") == "image" and el.get("storage_ref")
                                                ),
                                                None
                                            ),
                                            "referenced_elements_context": brief.get("referenced_elements_context"),
                                            "style_ref_storage_ref": brief.get("style_ref_storage_ref"),
                                            "style_seed": brief.get("style_seed"),
                                        }.items() if v is not None
                                    } or None
                                tool_result = await get_tool(tool_name).run(args, context=tool_context)
                                record = ToolCallRecord(
                                    tool_name=tool_name, args=args, ok=tool_result.ok,
                                    data=tool_result.data, error=tool_result.error,
                                )
                            except ToolNotFound as exc:
                                record = ToolCallRecord(
                                    tool_name=tool_name, args=args, ok=False, data={}, error=str(exc)
                                )
                            except Exception as exc:
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
                    # Real, live-found observability gap (2026-09-25, root-caused from a live
                    # failure this session where a specialist's tool call failed with "storage_ref
                    # not found" and NOTHING anywhere — this log line, this event, or the
                    # `tool_call_logs` table — had ever recorded what args the model actually
                    # passed, making it impossible to confirm whether the model sent a bad value or
                    # something else broke). Redacted via `redact_args` (never raw base64/binary),
                    # and only attached ON FAILURE — the successful, common case doesn't need it
                    # and args can be large.
                    log_extra = {
                        "_extra_specialist": specialist_name,
                        "_extra_tool": tool_name,
                        "_extra_ok": record.ok,
                        "_extra_iteration": iteration + 1,
                    }
                    if not record.ok:
                        log_extra["_extra_args"] = redact_args(args)
                        log_extra["_extra_error"] = record.error
                    log.info("specialist_tool_call", extra=log_extra)
                    emit(
                        "tool_call",
                        specialist=specialist_name,
                        tool=tool_name,
                        ok=record.ok,
                        **({"args": redact_args(args), "error": record.error} if not record.ok else {}),
                    )
                continue  # let the model see the real tool results before deciding what's next

            # No tool calls this turn -> the model considers itself done; parse its final decision.
            # Real, live-found bug (2026-09-30): a specialist with an EMPTY `required_output_fields`
            # (registry.py's own convention for "no schema to enforce" — e.g. `brand_asset_applier`,
            # whose prompt file has no `<output_format>` block at all and only ever reports success
            # via its tool calls) still got forced through `extract_json()` unconditionally here —
            # its own prompt never asked the model for JSON, so a genuinely correct plain-text
            # answer ("I've applied the logo to the tile...") failed to parse and raised
            # SpecialistFailed for no real reason. Skip the JSON requirement entirely for that case
            # and accept the raw text directly — `notes` matches the field name several other
            # specialists already use for a short free-text summary (Rules.md section 1: DRY).
            if not spec.required_output_fields:
                log.info(
                    "specialist_step_ok",
                    extra={
                        "_extra_specialist": specialist_name,
                        "_extra_tier": spec.tier.name,
                        "_extra_model": result.model,
                        "_extra_iterations": iteration + 1,
                        "_extra_tool_calls": len(tool_calls),
                        "_extra_no_json_schema": True,
                    },
                )
                emit("specialist_completed", specialist=specialist_name, tool_call_count=len(tool_calls))
                return AgenticStepResult(
                    specialist_name=specialist_name, model=result.model,
                    data={"notes": (result.text or "").strip()}, tool_calls=tool_calls,
                )

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

            # Code-enforced output contract: every specialist's real schema is either ALL of its
            # declared keys (even if a value is legitimately "" or false) OR the `error` escape
            # hatch above — verified by reading every specialist's own <output_format> block
            # directly, not assumed. KEY PRESENCE only, matching that real schema shape.
            missing_fields = [f for f in spec.required_output_fields if f not in parsed]
            if missing_fields:
                if _missing_fields_retries_left > 0:
                    _missing_fields_retries_left -= 1
                    log.warning(
                        "specialist_missing_required_output_fields",
                        extra={"_extra_specialist": specialist_name, "_extra_missing": missing_fields},
                    )
                    messages.append({"role": "assistant", "content": result.text or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Your final JSON is missing required field(s): {', '.join(missing_fields)}. "
                            "Return your final JSON again with ALL required fields present (or call a "
                            "tool if you're not actually done yet, or return ONLY {\"error\": \"...\"} "
                            "if you genuinely cannot fulfill the request)."
                        ),
                    })
                    continue
                raise SpecialistFailed(
                    specialist_name,
                    f"final response missing required field(s) after retry: {', '.join(missing_fields)}",
                )

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
    context: str | list[dict[str, Any]],
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
