"""
Tiered conversation memory (2026-10-05, explicit user design) — evolves this app's existing,
disclosed-weak memory (a flat, uncapped "last 6 turns" slice in `session_service.py`, plus an
unconditional semantic pull via `ChatMemoryService` on every single turn) into five real pieces:

- **Ledger**: structured working memory over the session's real canvas elements — a small, always-
  injected, CODE-derived summary (never LLM-authored, so it can't drift or hallucinate the way
  re-summarizing `brief.idea` every turn already does — see Memory.md's "numeric erosion" bug).
- **Window**: the latest raw turns, budgeted by message count AND token count, whichever hits
  first — not an unconditional flat slice.
- **Digests**: rolling summaries of turns that fall OUT of the window, so detail isn't simply
  dropped once a turn ages out — the actual fix for turn-40 forgetting a price mentioned in
  turn 4, which `brief.idea`'s own repeated resummarization already can't reliably preserve.
- **Recall**: NOT built here — see `services/tools/recall.py`, a real on-demand tool a specialist
  calls, replacing the old unconditional `get_relevant_history()` call every turn used to make.

Caching (the fifth piece from the design) isn't a module — it's an ordering discipline enforced at
each call site (static system prompt / tool schema first, this module's dynamic output appended
after), since Groq/OpenRouter cache a byte-identical prefix server-side with no client-side
`cache_control` markup to write.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...core.middleware.logging import get_logger
from ...models.canvas_element import CanvasElementModel
from ...models.chat_turn import ChatTurnModel
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider

log = get_logger(__name__)

# Same 300-char bound `leads/base.py`'s own `_truncate_description` uses for an interpolated
# description — duplicated, not imported, specifically to avoid a circular import (`leads/base.py`
# itself calls into this module's `ledger_block`/`digests_block`).
_MAX_TRUNCATED_CHARS = 300


def _truncate_description(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_TRUNCATED_CHARS:
        return text
    return text[:_MAX_TRUNCATED_CHARS].rstrip() + "…"

# ~4 characters per token is the standard, well-worn estimate for English text without a real
# tokenizer on hand (no `tiktoken`/provider-specific tokenizer dependency in this project) — good
# enough for a BUDGET (stop before things get too big), not for an exact count.
_CHARS_PER_TOKEN_ESTIMATE = 4
_DEFAULT_MAX_WINDOW_MESSAGES = 10
_DEFAULT_MAX_WINDOW_TOKENS = 8000
# A turn still inside the window but not among the most recent couple keeps only a short,
# artifact-reference form of its own long text — the "mask old tool outputs/images to short
# artifact references" requirement. The 2 most recent turns stay in full (today's real working
# context almost always needs the immediately preceding exchange verbatim).
_FULL_DETAIL_RECENT_TURN_COUNT = 2
# How many dropped (out-of-window) turns accumulate before they're worth paying for one real
# summarization call — too small wastes LLM calls on trivial exchanges, too large loses detail for
# too long before it's captured anywhere.
_DIGEST_TURN_THRESHOLD = 10
# Once this many closed digests exist, the oldest ones are merged into a single "epoch summary" —
# keeps the digest list itself from growing unboundedly over a very long session.
_DIGEST_MERGE_CAP = 3


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def _mask_turn(turn: ChatTurnModel) -> dict:
    """A turn outside the full-detail window: keep only short, bounded text — never the full
    `thinking_text` (often the single longest field on a turn) and a truncated assistant/user
    text, same cap `referenced_element_block` already uses for a long description."""
    return {
        "user": _truncate_description(turn.user_text or ""),
        "assistant": _truncate_description(turn.assistant_text or ""),
    }


def _full_turn(turn: ChatTurnModel) -> dict:
    return {"user": turn.user_text or "", "assistant": turn.assistant_text or ""}


def build_window(
    turns: list[ChatTurnModel],
    *,
    max_messages: int = _DEFAULT_MAX_WINDOW_MESSAGES,
    max_tokens: int = _DEFAULT_MAX_WINDOW_TOKENS,
) -> tuple[list[dict], list[ChatTurnModel]]:
    """Walks `turns` (oldest-first, as `ChatTurnRepository.list_for_session` already returns them)
    newest-first internally, keeping whichever of `max_messages`/`max_tokens` is hit FIRST — a real
    budget, not the previous unconditional `recent_turns[-6:]` slice (`session_service.py`, before
    this module existed). Returns `(window, dropped)` — `window` oldest-first for prompt
    interpolation (matches the shape `_recent_chat_history` already had), `dropped` is every turn
    that didn't make it in, oldest-first, for `conversation_memory.py`'s own digest bookkeeping to
    consume next."""
    kept: list[dict] = []
    kept_turns: list[ChatTurnModel] = []
    running_tokens = 0
    for i, turn in enumerate(reversed(turns)):
        if len(kept) >= max_messages:
            break
        entry = _full_turn(turn) if i < _FULL_DETAIL_RECENT_TURN_COUNT else _mask_turn(turn)
        entry_tokens = _estimate_tokens(entry["user"]) + _estimate_tokens(entry["assistant"])
        if kept and running_tokens + entry_tokens > max_tokens:
            break
        kept.append(entry)
        kept_turns.append(turn)
        running_tokens += entry_tokens

    window = list(reversed(kept))
    kept_ids = {t.id for t in kept_turns}
    dropped = [t for t in turns if t.id not in kept_ids]
    return window, dropped


@dataclass
class LedgerArtifact:
    kind: str
    label: str
    status: str
    last_op: str | None


def build_ledger(
    canvas_elements: list[CanvasElementModel],
    *,
    current_focus_id: str | None,
) -> dict:
    """Structured working memory over the session's REAL artifacts — deliberately built straight
    from `CanvasElementModel` rows (the one real source of truth already in the database), never
    re-derived by an LLM, so it can't drift the way a model asked to re-summarize "what exists so
    far" every turn eventually does (the same class of bug `brief.idea`'s own resummarization has —
    see Memory.md). Small and bounded by construction: one short line per artifact, not its full
    description/metadata."""
    artifacts: dict[str, dict] = {}
    for el in canvas_elements:
        label = (el.product_name or el.element_type or "artifact").strip()
        status = el.compliance_status or "unknown"
        last_op = el.produced_by_specialist
        artifacts[el.id] = LedgerArtifact(
            kind=el.element_type, label=label, status=status, last_op=last_op
        ).__dict__
    return {"artifacts": artifacts, "current_focus": current_focus_id}


def ledger_block(ledger: dict | None) -> str:
    """Short prose rendering of `build_ledger`'s output, for interpolation into a specialist's
    context — bounded regardless of how many artifacts a long session has accumulated (previews at
    most 5, same "...and N more" pattern `available_context_block` already uses for referenced
    elements)."""
    if not ledger or not ledger.get("artifacts"):
        return ""
    artifacts: dict = ledger["artifacts"]
    focus = ledger.get("current_focus")
    lines = [f"- {len(artifacts)} artifact(s) exist so far in this session."]
    for i, (el_id, a) in enumerate(artifacts.items()):
        if i >= 5:
            lines.append(f"  ...and {len(artifacts) - 5} more.")
            break
        marker = " (current focus)" if el_id == focus else ""
        lines.append(f"  - {a['kind']} \"{a['label']}\" — {a['status']}{marker}")
    return "\n" + "\n".join(lines)


async def summarize_turns_to_digest(turns: list[ChatTurnModel]) -> str:
    """One real, cheap-tier (`TIER_1`) summarization call over a bounded span of turns that just
    fell out of the window — the actual fix for the documented "numeric erosion" bug: a digest is
    written ONCE from the real turns it covers, not re-derived from an already-lossy prior summary
    on every later turn the way `brief.idea` is. A real failure here degrades to an honest, short
    placeholder rather than silently losing the span — a dropped digest is a worse loss than a
    terse one."""
    if not turns:
        return ""

    transcript = "\n".join(
        f"User: {t.user_text}\nAssistant: {t.assistant_text or ''}" for t in turns
    )
    try:
        llm = get_llm_provider()
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=(
                "Summarize this span of a marketing-campaign conversation in 2-4 sentences. "
                "Preserve every concrete fact exactly as stated — prices, percentages, names, "
                "specific decisions — never round, paraphrase, or drop a number. Do not add "
                "anything not actually said."
            ),
            messages=[{"role": "user", "content": transcript}],
            max_tokens=300,
        )
        return result.text.strip()
    except Exception as e:
        log.warning("digest_summarization_failed", extra={"_extra_error": str(e)})
        first_user = turns[0].user_text or ""
        return f"[{len(turns)} earlier turns, starting: {_truncate_description(first_user)}]"


async def merge_digests(digest_summaries: list[str]) -> str:
    """Collapses the oldest digests into one "epoch summary" once the digest list itself grows
    past `_DIGEST_MERGE_CAP` — keeps the memory structure itself bounded over a very long session,
    same shape as the summarization call above, just over digests-of-digests instead of raw
    turns."""
    if not digest_summaries:
        return ""
    if len(digest_summaries) == 1:
        return digest_summaries[0]

    joined = "\n\n".join(f"Earlier summary {i + 1}: {s}" for i, s in enumerate(digest_summaries))
    try:
        llm = get_llm_provider()
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=(
                "Merge these sequential summaries of one conversation into a single shorter "
                "summary, 3-5 sentences. Preserve every concrete fact exactly — prices, "
                "percentages, names, specific decisions — never round, paraphrase, or drop a "
                "number that appears in more than one summary."
            ),
            messages=[{"role": "user", "content": joined}],
            max_tokens=400,
        )
        return result.text.strip()
    except Exception as e:
        log.warning("digest_merge_failed", extra={"_extra_error": str(e)})
        return " ".join(digest_summaries)


async def update_digests(
    existing_digests: list[dict],
    dropped_turns: list[ChatTurnModel],
    *,
    digest_turn_threshold: int = _DIGEST_TURN_THRESHOLD,
    merge_cap: int = _DIGEST_MERGE_CAP,
) -> list[dict]:
    """The real orchestration step: folds newly-dropped turns into the currently-open digest (or
    starts one), closes it once it covers `digest_turn_threshold` turns, and merges the oldest
    digests together once there are more than `merge_cap` of them. `existing_digests` is
    `brief["memory_digests"]` as persisted from the prior turn — each entry
    `{"epoch": int, "summary": str, "covered_turn_ids": [...]}`.

    `dropped_turns` is `build_window`'s OWN `dropped` list — re-derived from the session's ENTIRE
    turn history every single call (session_service.py re-fetches every turn and re-runs
    `build_window` fresh each turn, it never keeps an incremental delta across calls). That means
    every turn this function might ever need to summarize is always already present in
    `dropped_turns`, every time — no cross-call cache of turn OBJECTS is needed (or safe: turns
    aren't JSON-serializable, and `existing_digests` round-trips through `session.brief`, a plain
    JSON column). Only `covered_turn_ids` (plain strings) needs to persist across calls."""
    digests = [dict(d) for d in existing_digests]
    covered_ids = {tid for d in digests for tid in d.get("covered_turn_ids", [])}
    turns_by_id = {t.id: t for t in dropped_turns}
    new_turns = [t for t in dropped_turns if t.id not in covered_ids]

    open_digest = digests[-1] if digests and digests[-1].get("open") else None
    if open_digest is not None:
        open_digest["covered_turn_ids"] = open_digest.get("covered_turn_ids", []) + [t.id for t in new_turns]
    elif new_turns:
        digests.append({
            "epoch": len(digests),
            "summary": "",
            "covered_turn_ids": [t.id for t in new_turns],
            "open": True,
        })
        open_digest = digests[-1]

    if open_digest is not None and len(open_digest["covered_turn_ids"]) >= digest_turn_threshold:
        pending = [turns_by_id[tid] for tid in open_digest["covered_turn_ids"] if tid in turns_by_id]
        open_digest["summary"] = await summarize_turns_to_digest(pending)
        open_digest["open"] = False

    closed = [d for d in digests if not d.get("open")]
    still_open = [d for d in digests if d.get("open")]
    if len(closed) > merge_cap:
        to_merge, keep = closed[: len(closed) - merge_cap + 1], closed[len(closed) - merge_cap + 1:]
        merged_summary = await merge_digests([d["summary"] for d in to_merge])
        merged = {
            "epoch": to_merge[0]["epoch"],
            "summary": merged_summary,
            "covered_turn_ids": [tid for d in to_merge for tid in d["covered_turn_ids"]],
            "open": False,
        }
        closed = [merged] + keep

    return closed + still_open


def digests_block(digests: list[dict] | None) -> str:
    """Short prose rendering of every closed digest, oldest first — "every summary plus the
    current raw window," per the design. An open (not-yet-summarized) digest contributes nothing
    here; its real turns are still individually visible via `build_window`'s `dropped` list until
    it closes."""
    if not digests:
        return ""
    closed = [d for d in digests if d.get("summary")]
    if not closed:
        return ""
    lines = [f"- Earlier in this session (summary {d['epoch'] + 1}): {d['summary']}" for d in closed]
    return "\n" + "\n".join(lines)
