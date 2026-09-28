"""
Shared retry-with-backoff HTTP call logic for OpenAI-compatible /chat/completions APIs —
OpenRouter and Groq both expose this exact request/response shape. Extracted once so both
provider files share it rather than duplicating the same ~60-line retry block (Rules.md section 1:
DRY, and the genai_build guide's explicit "copying a block that already exists elsewhere" mistake).

Not a vendor SDK itself — each provider file still owns its own base URL, API key, and model-tier
resolution, and is still the only place that decides which vendor it's really talking to.

Two request modes, both ending in the exact same `LLMResult` shape (2026-09-21, per the user's
explicit ask to show real LLM "thinking" live): a real streamed request (`stream: true`,
`STREAM_LLM_THINKING_ENABLED=true` and a caller-supplied `on_delta`) reassembles the response from
real incremental chunks, calling `on_delta` with each real text fragment as it arrives; otherwise
the original single-shot request/response call runs unchanged. Streaming reassembly is the
genuinely riskier path — an OpenAI-compatible streamed tool call arrives as fragments keyed by
`index` (a partial `id`/`function.name` once, then repeated `function.arguments` string chunks to
concatenate) rather than one complete object — so `stream_llm_thinking_enabled=false` (or no
`on_delta` given at all) always falls back to the original, already-proven call path, never a
half-migrated in-between.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from ...core.config import settings
from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMResult

log = get_logger(__name__)


def _try_recover_fake_final_tool_call(error_body: str) -> str | None:
    """
    Some free tool-calling models occasionally hallucinate a fake tool call (named "json", "JSON",
    "final_answer", etc.) to represent their final answer instead of just returning plain content
    with no tool_calls — a real quirk observed live on Groq's gpt-oss-120b under real tool-calling
    load (Memory.md, Phase 2), with the exact fake name varying between calls (case differed
    between two separate live occurrences), so a prompt instruction alone can't reliably prevent
    it — a stochastic model won't always obey a prompt rule.

    The vendor's own rejection still carries the model's real intended answer in
    `error.failed_generation` (Groq's shape) — recovering it here turns a hard failure into the
    exact same successful outcome a well-behaved final answer would have produced, without
    fabricating anything: the JSON returned is verbatim what the model itself generated.
    """
    try:
        err = (json.loads(error_body).get("error")) or {}
    except json.JSONDecodeError:
        return None

    if "tool call validation failed" not in str(err.get("message", "")).lower():
        return None

    failed_generation = err.get("failed_generation")
    if not failed_generation:
        return None

    try:
        parsed = json.loads(failed_generation)
    except json.JSONDecodeError:
        return None

    args = parsed.get("arguments")
    if isinstance(args, dict):
        return json.dumps(args)
    if isinstance(args, str):
        return args
    return None


def _merge_streamed_tool_call_delta(accum: dict[int, dict[str, Any]], deltas: list[dict[str, Any]]) -> None:
    """Folds one streamed chunk's `delta.tool_calls` fragments into the running per-index
    accumulator — OpenAI-compatible streaming sends a tool call's `id`/`function.name` once (in
    whichever chunk first mentions that index) and its `function.arguments` as repeated partial
    string fragments to concatenate, never one complete object like the non-streaming shape."""
    for fragment in deltas:
        index = fragment.get("index", 0)
        call = accum.setdefault(
            index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if fragment.get("id"):
            call["id"] = fragment["id"]
        fn_fragment = fragment.get("function") or {}
        if fn_fragment.get("name"):
            call["function"]["name"] = fn_fragment["name"]
        if fn_fragment.get("arguments"):
            call["function"]["arguments"] += fn_fragment["arguments"]


class _RetryableStreamError(ProviderUnavailable):
    """Distinguishes a 429/5xx (worth backing off and retrying, same policy as the non-streaming
    path) from a genuine 4xx client error (fatal, raised as plain `ProviderUnavailable` instead —
    retrying a real bad request blindly would just repeat the same failure)."""


async def _stream_one_attempt(
    *, client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any],
    provider_name: str, model: str, on_delta: Callable[[str], None], start: float,
) -> LLMResult:
    """One real streamed HTTP attempt — raises `_RetryableStreamError` for 429/5xx (the caller's
    retry loop backs off and tries again) or plain `ProviderUnavailable` for a genuine 4xx
    (fatal, matching the non-streaming path's exact policy)."""
    async with client.stream("POST", url, headers=headers, json={**body, "stream": True}) as resp:
        if resp.status_code == 429:
            raise _RetryableStreamError(provider_name, f"{model} rate limited")
        if resp.status_code >= 500:
            raise _RetryableStreamError(provider_name, f"{model} HTTP {resp.status_code}")
        if resp.status_code >= 400:
            error_text = (await resp.aread()).decode(errors="replace")
            recovered = _try_recover_fake_final_tool_call(error_text)
            if recovered is not None:
                log.warning(
                    "llm_http_fake_tool_call_recovered",
                    extra={"_extra_provider": provider_name, "_extra_model": model},
                )
                return LLMResult(text=recovered, model=model, stop_reason="stop")
            raise ProviderUnavailable(provider_name, f"{model} HTTP {resp.status_code}: {error_text[:300]}")

        text_parts: list[str] = []
        tool_call_accum: dict[int, dict[str, Any]] = {}
        stop_reason: str | None = None
        usage: dict[str, Any] = {}
        raw_line_count = 0
        # A real, live-found bug (2026-09-21): Groq's SSE stream can send a genuine
        # `event: error` frame mid-stream (confirmed live — a real request under load, not a
        # guess), immediately followed by a `data: {...}` line carrying the actual error, not a
        # content chunk. The original parser only ever looked for `data: ` lines and silently
        # skipped anything else, including this one — so the very next `data:` line got treated
        # as a normal (empty) chunk instead of a real failure, and the whole call quietly
        # "succeeded" with zero content and zero tool calls. Reproduced directly: 4 of 5 real
        # calls failed this exact way under rapid repeated load; 0 of 3 failed with streaming
        # disabled on the same scenario.
        pending_error_event = False

        async for line in resp.aiter_lines():
            raw_line_count += 1
            if line.startswith(":"):
                continue  # a real SSE keep-alive comment (e.g. OpenRouter's own ": PROCESSING"
                # line during a slow generation) — part of the spec, never a real problem, so
                # never worth a warning; only a genuinely unrecognized line still gets one below.
            if line.startswith("event: "):
                pending_error_event = line[len("event: ") :].strip() == "error"
                continue
            if not line.startswith("data: "):
                if line.strip():
                    log.warning(
                        "llm_stream_unexpected_line",
                        extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_line": line[:200]},
                    )
                continue
            payload = line[len("data: ") :].strip()
            if payload == "[DONE]":
                break
            if pending_error_event:
                pending_error_event = False
                # The most common real cause, confirmed live: the same "fake tool call named
                # 'json'" quirk the non-streaming path already recovers from via
                # `_try_recover_fake_final_tool_call` (Memory.md, Phase 2) — Groq rejects it as a
                # genuine mid-stream error instead of a plain 4xx here, but the model's real
                # intended answer is still recoverable from the same `failed_generation` field.
                # Try that first — free, and avoids a wasted retry — before falling back to a
                # retryable failure for anything that isn't this specific, already-understood shape.
                recovered = _try_recover_fake_final_tool_call(payload)
                if recovered is not None:
                    log.warning(
                        "llm_stream_fake_tool_call_recovered",
                        extra={"_extra_provider": provider_name, "_extra_model": model},
                    )
                    return LLMResult(text=recovered, model=model, stop_reason="stop")
                log.warning(
                    "llm_stream_error_event",
                    extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_payload": payload[:300]},
                )
                raise _RetryableStreamError(provider_name, f"{model} stream error: {payload[:300]}")
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue  # a real, if rare, malformed frame — skip it rather than abort the whole stream

            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                stop_reason = choice["finish_reason"]
            if delta.get("content"):
                text_parts.append(delta["content"])
                on_delta(delta["content"])
            if delta.get("tool_calls"):
                _merge_streamed_tool_call_delta(tool_call_accum, delta["tool_calls"])

        # A real, live-found failure shape (2026-09-21), in two variants: OpenRouter sometimes
        # returns a genuine HTTP 200 with only a couple of raw lines, no `choices` ever carrying
        # content or tool_calls, and no `finish_reason` at all. The self-hosted Ollama model has
        # its own variant of the same problem — a normal `finish_reason: "stop"` chunk, but zero
        # content deltas ever sent (confirmed live: 5 raw lines total, nothing but role+finish).
        # Either way the result is unusable — our runner.py always needs either tool_calls to
        # continue on, or real text to parse as the final answer — so both count as retryable
        # regardless of what `stop_reason` came back, rather than silently returning an empty
        # "successful" LLMResult that only fails much later as an opaque "empty model response"
        # deep in the specialist parser, after discarding that run's real prior tool calls.
        if not text_parts and not tool_call_accum:
            raise _RetryableStreamError(
                provider_name,
                f"{model} stream ended with no content (raw_lines={raw_line_count}, stop_reason={stop_reason})",
            )

        log.info(
            "llm_http_call",
            extra={
                "_extra_provider": provider_name,
                "_extra_model": model,
                "_extra_tokens_in": usage.get("prompt_tokens"),
                "_extra_tokens_out": usage.get("completion_tokens"),
                "_extra_cost": usage.get("cost"),
                "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                "_extra_streamed": True,
                "_extra_raw_lines": raw_line_count,
                "_extra_text_len": len("".join(text_parts)),
                "_extra_tool_call_count": len(tool_call_accum),
                "_extra_stop_reason": stop_reason,
            },
        )
        return LLMResult(
            text="".join(text_parts),
            model=model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            tool_calls=[tool_call_accum[i] for i in sorted(tool_call_accum)],
            stop_reason=stop_reason,
        )


async def call_openai_compatible_chat(
    *,
    provider_name: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    retries: int = 2,
    on_delta: Callable[[str], None] | None = None,
    strip_images: bool = False,
) -> LLMResult:
    """Retry-with-backoff on ONE model against an OpenAI-compatible /chat/completions endpoint.
    Raises ProviderUnavailable if this model can't complete the call after its retry budget —
    the caller decides whether to fall back to another model or another provider entirely."""
    
    if strip_images:
        safe_messages = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                safe_messages.append({"role": msg["role"], "content": "\n".join(text_parts)})
            else:
                safe_messages.append(msg)
        messages = safe_messages

    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if tools:
        body["tools"] = tools

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/chat/completions"
    use_streaming = settings.stream_llm_thinking_enabled and on_delta is not None

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                if use_streaming:
                    return await _stream_one_attempt(
                        client=client, url=url, headers=headers, body=body,
                        provider_name=provider_name, model=model, on_delta=on_delta, start=start,
                    )

                resp = await client.post(url, headers=headers, json=body)

            if resp.status_code == 429:
                last_error = ProviderUnavailable(provider_name, f"{model} rate limited")
                if attempt == retries:
                    break
                wait = 1.5 * (attempt + 1)
                log.warning(
                    "llm_http_rate_limited",
                    extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_wait_s": wait},
                )
                emit("llm_retry", provider=provider_name, model=model, wait_s=wait, reason="rate_limited")
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 500:
                last_error = ProviderUnavailable(provider_name, f"{model} HTTP {resp.status_code}")
                if attempt == retries:
                    break
                wait = 1.0 * (attempt + 1)
                log.warning(
                    "llm_http_5xx",
                    extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_status": resp.status_code},
                )
                emit("llm_retry", provider=provider_name, model=model, wait_s=wait, reason="server_error")
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                recovered = _try_recover_fake_final_tool_call(resp.text)
                if recovered is not None:
                    log.warning(
                        "llm_http_fake_tool_call_recovered",
                        extra={"_extra_provider": provider_name, "_extra_model": model},
                    )
                    return LLMResult(text=recovered, model=model, stop_reason="stop")
                raise ProviderUnavailable(
                    provider_name, f"{model} HTTP {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            usage = data.get("usage") or {}

            # Real, live-found gap (2026-09-28): the streaming path already guards against this
            # exact shape (`_stream_one_attempt` above) — a genuine HTTP 200 with no content and no
            # tool_calls — but this non-streaming path never got the same check, so it used to
            # return a "successful" LLMResult with text="" straight through, only failing much
            # later as an opaque "empty model response" deep in the specialist parser. Treated the
            # same as the 429/5xx branches above: retryable within this call's own budget.
            if not msg.get("content") and not msg.get("tool_calls"):
                last_error = ProviderUnavailable(provider_name, f"{model} returned empty content")
                if attempt == retries:
                    break
                wait = 1.0 * (attempt + 1)
                log.warning(
                    "llm_http_empty_response",
                    extra={"_extra_provider": provider_name, "_extra_model": model},
                )
                emit("llm_retry", provider=provider_name, model=model, wait_s=wait, reason="empty_response")
                await asyncio.sleep(wait)
                continue

            log.info(
                "llm_http_call",
                extra={
                    "_extra_provider": provider_name,
                    "_extra_model": model,
                    "_extra_tokens_in": usage.get("prompt_tokens"),
                    "_extra_tokens_out": usage.get("completion_tokens"),
                    "_extra_cost": usage.get("cost"),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return LLMResult(
                text=msg.get("content") or "",
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                tool_calls=msg.get("tool_calls") or [],
                stop_reason=choice.get("finish_reason"),
            )
        except _RetryableStreamError as exc:
            # Raised by _stream_one_attempt only for 429/5xx — same backoff the non-streaming
            # path applies inline above, so both modes share one retry policy. A genuine 4xx
            # raises plain ProviderUnavailable instead, which is NOT caught here and propagates
            # immediately, matching the non-streaming path's own "don't retry a real bad request"
            # behavior.
            last_error = exc
            wait = 1.5 * (attempt + 1)
            log.warning(
                "llm_http_stream_retry",
                extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_reason": exc.message},
            )
            emit("llm_retry", provider=provider_name, model=model, wait_s=wait, reason="stream_error")
            await asyncio.sleep(wait)
            continue
        except httpx.TimeoutException as exc:
            last_error = ProviderUnavailable(provider_name, f"{model} timeout: {exc}")
            log.warning(
                "llm_http_timeout",
                extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_attempt": attempt + 1},
            )
        except httpx.RequestError as exc:
            # Everything else transport-level (refused connections most of all — e.g. a self-hosted
            # local_llm server that isn't running) — a real, live-found gap: this used to only catch
            # TimeoutException, so a plain ConnectError propagated raw instead of becoming a
            # ProviderUnavailable, breaking router.py's fallback-on-failure catch entirely.
            last_error = ProviderUnavailable(provider_name, f"{model} request failed: {exc}")
            log.warning(
                "llm_http_request_error",
                extra={
                    "_extra_provider": provider_name,
                    "_extra_model": model,
                    "_extra_attempt": attempt + 1,
                    "_extra_error": str(exc),
                },
            )

    raise last_error or ProviderUnavailable(provider_name, f"{model} failed after retries")
