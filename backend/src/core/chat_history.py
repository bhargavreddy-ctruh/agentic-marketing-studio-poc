"""
Real conversation history for LLM calls (2026-09-22, per an explicit user ask: "make sure the llm
has chat history context cache, so it can work in a session").

A real, live-found gap: this app already persists every real turn verbatim (`ChatTurnModel`,
`session_service.py`), but nothing ever fed it back into an actual LLM call — `ideation_service.py`
and `orchestrator.py` each built a single stateless user message from `brief.idea`, a summary the
model itself re-writes every turn. That summary staying lossy was a deliberate, documented tradeoff
(a real "numeric erosion" bug — resummarizing repeatedly lost real figures like "$1500/12% off"),
but the fix for THAT bug never replaced real memory with something better, it just accepted losing
it.

This is the actual fix: real, VERBATIM past turns (never re-summarized, so they can't erode the
same way) become real conversation history — alternating user/assistant messages — with whatever
the caller was already going to ask as the final user turn. `session_service.py` reads the real
`chat_turns` table and hands the last few turns to the graph as `brief["_recent_chat_history"]`
(read-only, never persisted back — see that file's own comment); this module turns that into the
real `messages` array an LLM call actually sends.
"""
from __future__ import annotations


def build_history_messages(brief: dict, final_user_content: str) -> list[dict[str, str]]:
    """Real prior turns (if any) + the caller's own current-turn content as the final user
    message — the same context every existing call already built, just no longer standing alone.
    Empty/missing history degrades to exactly the old single-message behavior, unchanged."""
    messages: list[dict[str, str]] = []
    for turn in brief.get("_recent_chat_history") or []:
        user_text = turn.get("user")
        if user_text:
            messages.append({"role": "user", "content": user_text})
        assistant_text = turn.get("assistant")
        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})
    messages.append({"role": "user", "content": final_user_content})
    return messages
