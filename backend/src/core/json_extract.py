"""
Best-effort JSON extraction from LLM text output — free-tier models are more likely than a
frontier model to wrap JSON in prose or a code fence. Ported pattern from the existing
agentic_flow codebase's flow_builder `_extract_json` (Rules.md section 6).
"""
from __future__ import annotations

import json
import re
from typing import Any


def _close_truncated_json(text: str) -> str | None:
    """Real, live-found failure (2026-09-30, palette_strategist): a verbose model response ran
    past its token budget MID-STRING, cutting the response off before the JSON object's closing
    quote/brace ever arrived — every candidate above requires a genuinely complete, balanced
    structure, so a truncated-but-otherwise-real answer got discarded entirely instead of used.
    This walks the text character-by-character (respecting escapes, so a `\\"` inside a string
    never mistakenly counts as the string's end) and closes whatever was still open when the text
    ran out: an unterminated string first, then any still-open `{`/`[` in reverse order. It never
    invents content — only closes structure that's already there — so a genuinely malformed
    response (mismatched brackets, garbage before any `{`) still correctly fails to parse and
    falls through to the real error below, same as before this existed."""
    if "{" not in text:
        return None
    repaired = text[text.index("{"):]

    in_string = False
    escape = False
    stack: list[str] = []
    for ch in repaired:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()

    if not in_string and not stack:
        return None  # already balanced — nothing to repair, let the real candidates handle it

    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def _escape_unescaped_quotes_in_strings(text: str) -> str | None:
    """Real, live-found failure (2026-10-07, illustrator's final response — a DIFFERENT bug than
    the control-character one `strict=False` above already fixed): a model sometimes embeds a
    literal, un-escaped `"` INSIDE a string value — e.g. a prompt field describing on-screen text
    like `a sign reading "SALE"` instead of escaping it as `\\"SALE\\"`. That quote closes the JSON
    string early; everything after it until the next real quote is then raw text the parser never
    expects, which reliably fails with "Expecting ',' delimiter" or similar, often hundreds of
    characters into an otherwise well-formed response — `strict=False` doesn't help here, since the
    text isn't a disallowed control character, it's a structurally-misplaced ordinary character.

    There is no way to know with certainty which quote the model "meant" to close the string at —
    but in practice a GENUINE closing quote is always immediately followed (after optional
    whitespace) by a real JSON structural character: `,`, `}`, `]`, or `:`. A quote NOT followed by
    one of those is almost always a stray one that should have been escaped. Walks the text
    character-by-character (respecting real escapes, same approach `_close_truncated_json` above
    already uses) and escapes exactly those stray quotes, leaving genuine structural quotes alone —
    never invents or removes content, only repairs misplaced escaping."""
    if '"' not in text:
        return None
    out: list[str] = []
    in_string = False
    escape = False
    changed = False
    n = len(text)
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j >= n or text[j] in ",}]:":
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
                    changed = True
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out) if changed else None


_THOUGHT_BLOCK = re.compile(r"<thought>[\s\S]*?</thought>", re.IGNORECASE)
_UNCLOSED_THOUGHT_PREFIX = re.compile(r"^<thought>[\s\S]*", re.IGNORECASE)


def _strip_thought_blocks(text: str) -> str:
    """Every "Think Before Acting" prompt in this codebase (ideation, runner.py specialists, the
    orchestrator) deliberately asks the model to reason inside a `<thought>...</thought>` block
    before its JSON — live-found bug (2026-10-06): the brace-matching candidate below is greedy
    (`\\{[\\s\\S]*\\}`), so if the model's own reasoning text mentions a `{` anywhere (e.g.
    describing JSON-ish structure, or quoting the user's message), that candidate spans from the
    thought block's first `{` all the way to the real answer's last `}` — garbage in the middle,
    reliably failing to parse. Stripping the whole thought block first (it's well-formed — the
    model reliably closes its own tag) means the candidates below only ever see the real answer."""
    stripped = _THOUGHT_BLOCK.sub("", text).strip()
    if stripped:
        return stripped
    # A response that got cut off mid-thought (no closing tag) leaves nothing real behind —
    # dropping the whole prefix here is correct: there was no JSON after it to find anyway.
    return _UNCLOSED_THOUGHT_PREFIX.sub("", text).strip()


def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    text = _strip_thought_blocks(text) or text

    # Real, live-found bug (2026-09-21): a smaller model sometimes wraps its answer in an EMPTY
    # code fence (` ```json``` ` with nothing inside). The old code unconditionally overwrote
    # `text` with the fence's (possibly empty) captured group AFTER the emptiness check above had
    # already passed — `json.loads("")` then raised the exact "Expecting value: line 1 column 1
    # (char 0)" seen live, misreported as "could not parse JSON" instead of the real "empty model
    # response". Candidates are now additive (raw text first, then a genuinely non-empty fence
    # capture, then a brace match) instead of destructively replacing `text`.
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence and fence.group(1).strip():
        candidates.append(fence.group(1).strip())
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        candidates.append(brace.group(0))

    # Last resort, tried only after every genuinely-complete candidate above has failed (checked
    # further down) — repairing truncated structure is a real degrade, not the preferred path.
    repair_source = fence.group(1).strip() if fence and fence.group(1).strip() else text
    repaired = _close_truncated_json(repair_source)
    if repaired:
        candidates.append(repaired)

    # Stray-quote repair — tried against every candidate gathered so far (a truncation repair can
    # ALSO have a stray quote earlier in the same response; trying it against each candidate,
    # not just raw `text`, covers that combination instead of only the single most common case).
    for candidate in list(candidates):
        quote_fixed = _escape_unescaped_quotes_in_strings(candidate)
        if quote_fixed and quote_fixed not in candidates:
            candidates.append(quote_fixed)

    last_error: Exception | None = None
    # Real, live-found bug (2026-10-06, overlay_artist's own final-response parse): a model
    # sometimes writes a literal raw newline/control character inside a JSON string value (e.g.
    # explaining its reasoning across multiple lines inside a `"reasoning": "..."` field) instead
    # of the escaped `\n` the JSON spec requires — strict `json.loads` rejects the whole response
    # with "Invalid control character" even though every other character is well-formed.
    # `strict=False` is the stdlib's own documented escape hatch for exactly this: it accepts
    # literal control characters inside strings instead of hard-failing on them.
    decoder = json.JSONDecoder(strict=False)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            last_error = exc
            # Real, live-found case (2026-09-21): a smaller model sometimes produces one genuinely
            # valid JSON object followed by MORE text (a repeated object, stray commentary) —
            # `json.loads` rejects the whole string as "Extra data" even though the leading object
            # is perfectly valid. `raw_decode` parses just that leading value and reports where it
            # stopped, so trailing junk no longer sinks an otherwise-correct answer.
            try:
                parsed, _ = decoder.raw_decode(candidate.lstrip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    
    # Provide a snippet of the text in the error message so we can see what the model actually returned
    text_snippet = (text[:100] + '...') if len(text) > 100 else text
    raise ValueError(f"could not parse JSON from model response (starts with: {text_snippet!r}): {last_error}")
