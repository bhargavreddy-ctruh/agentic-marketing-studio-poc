"""
The Ideation node — Architecture.md section 1d, now real (Phase 1 replaces the Phase 0
placeholder). Uses Tier 1 (small/fast, cheap) since deciding "is this brief specific enough" and
proposing a couple of creative directions doesn't need frontier-model judgment — that's reserved
for the Illustrator (Rules.md's model-tiering principle in practice).
"""
from __future__ import annotations

import re

from ...core.approval import is_cancel
from ...core.chat_history import build_history_messages
from ...core.events import emit
from ...core.exceptions import SpecialistFailed
from ...core.json_extract import extract_json
from ...core.middleware.logging import get_logger
from ...providers.llm.base import ModelTier
from ...providers.llm.router import get_llm_provider
from ...providers.observability.langsmith import traceable
from ..orchestration.state import GraphState

log = get_logger(__name__)

_SYSTEM_PROMPT = """You are the ideation partner for a product marketing creative studio.

Given a running brief (what the user has told you so far) and their latest message, decide:
1. Is there enough here to start generating something (a clear subject/idea, even if brand and
   product details are still missing — those can be added later)? This app can produce a still
   image, a short video, OR a standalone spoken voiceover/audio clip — a request for just audio
   (e.g. "generate a voiceover for our tagline", "make an audio clip saying...") is just as
   in-scope and ready-to-proceed as an image or video request; do not treat it as needing more
   detail just because it isn't visual. One real, disclosed limit worth surfacing if directly
   relevant: only spoken voiceover is possible, never music.
2. If not, propose 2 concrete, pickable creative directions (not open questions) plus always allow
   free text instead.
3. Separately from subject/content clarity, think about VISUAL MOOD/STYLE for anything visual (a
   poster, a campaign key visual, an ad, a still image) whenever the user hasn't already specified
   one. This is a real creative judgment call, not busywork — the same way an experienced designer
   would react to "a poster for our headliner" with a strong point of view on tone (e.g. a concert
   headliner poster calls for something bold and electric — dramatic lighting, high-contrast color,
   festival energy — not a guess made in a vacuum). Two outcomes, and prefer the first:
   - USUALLY: confidently pick ONE fitting mood/style yourself, based on the subject/use-case/genre,
     and weave it directly into `merged_brief.idea` as a concrete visual-direction clause (lighting,
     color feel, composition energy — not just an adjective). Also fill `style_note` with one short,
     warm sentence telling the user what you went with and why, so they can react/redirect instead
     of being surprised later (e.g. "Going bold and electric for this — dramatic stage lighting,
     high-contrast color, real festival energy.").
   - ONLY when multiple genuinely different, similarly-strong directions fit and picking wrong would
     mean redoing real work (e.g. a headliner poster could honestly go bold/electric, cinematic/
     moody, or minimalist/typographic — all valid, all different): set `ready: false` and offer
     those as pickable `options` instead of guessing, same shape as a subject-clarity option
     (label = short style name, description = what it looks like). Do not do this for minor/generic
   requests where one sensible default is obviously fine — most requests should NOT stop to ask.

4. BRAND & PRODUCT DNA (CRITICAL): Both Brand and Product guidelines MUST exist for every session.
   - Look at the running brief's "guardrails" array. If there are no brand and product guardrails present, and the user hasn't provided any in their latest message, you MUST set `ready: false` and explicitly ask them to provide their brand and product guidelines (or to select them from the UI).
   - If the user's message CONTAINS new information about the brand (e.g. "our brand colors are...", "we are a modern...") or the product (e.g. "the product is a new shoe...", "never show XYZ in the product"), extract these as distinct, actionable rules in `new_guardrails`.
   - The same applies if they explicitly ask to update their Product DNA from the chat.

5. **NO ASSUMPTIONS ON VAGUE INPUTS:** Never assume anything that is not explicitly stated. If the user attaches an image but doesn't explain how to use it, or if their request is too vague (e.g., "create a sale post" without specifying the product or brand), DO NOT guess. You MUST set `ready: false` and ask for clarification.
6. **DYNAMIC GUARDRAILS FIRST:** It is very important to create guardrails first based on the user's inputs. If the user specifies any strict requirement, constraint, style preference, or describes their product/brand (e.g. "winter campaign", "must be red"), extract these immediately into `new_guardrails`.

Never ask more than one thing at a time — if content is unclear, resolve that before ever touching style. Prefer proposing options over asking an open question. Keep your tone warm and encouraging.

Stay strictly on task: you only help plan and generate product marketing visuals and audio.

Return ONLY JSON:
{
  "ready": true or false,
  "merged_brief": {"idea": "a clear, one-paragraph synthesis of the brief so far, including any visual mood/style direction you chose"},
  "style_note": "one short sentence announcing the mood/style you picked, only when ready is true and this was a visual request — omit/empty otherwise",
  "message": "a short line stating what's still needed, only used when ready is false",
  "options": [{"id": "short_id", "label": "Bold label", "description": "one-line rationale"}],
  "new_guardrails": [{"source": "brand" or "product", "rule": "The explicit rule", "scope": "all"}] // only if the user provided new brand/product guidelines in their message
}
"""

# A plain greeting on a brand-new session (nothing in the brief yet) isn't a vague creative brief
# needing clarification — it's someone saying hello before they've said anything at all. Handled
# as a real, deterministic, Tier 0 fast path (like `color_palette_extractor`'s own no-model-call
# pattern) rather than trusted to an LLM prompt instruction: free, instant, and never subject to
# the same kind of instruction-following drift already found and fixed elsewhere this session
# (Memory.md, 2026-09-21) — a plain string match cannot misinterpret "hi" as a creative request.
_GREETINGS = {
    "hi", "hello", "hey", "hiya", "yo", "sup", "hi there", "hello there", "hey there",
    "good morning", "good afternoon", "good evening", "greetings",
}
_INTRO_MESSAGE = (
    "Hi! 👋 I'm your creative partner for product marketing visuals and audio — tell me what "
    "you'd like to make (a still image, a short video, or a spoken voiceover clip) and I'll help "
    "shape it into something ready to generate. For example: \"a hero shot of a red running "
    "sneaker on a white background\", \"a 10-second video ad for a new coffee brand\", or "
    "\"a voiceover clip announcing our summer sale.\" What would you like to create?"
)


def _is_bare_greeting(brief: dict, user_message: str) -> bool:
    # `brief` is never a bare `{}` here even on a session's very first turn — session_service.py
    # always injects `approval_mode` (and, once an element exists, several more scratch fields —
    # session_service.py's own `_scratch_keys`) before invoking the graph. "Nothing accumulated
    # yet" really means no `idea` has been synthesized, not an empty dict (a real bug caught live
    # testing this fast path: `not brief` was always False, so this never actually fired).
    return not brief.get("idea") and user_message.strip().strip("!.").lower() in _GREETINGS


# A real, deterministic backstop (2026-09-22) for `_FOLLOWUP_CLARITY_PROMPT`'s own documented
# "CRITICAL EXCEPTION" below — a real, live-found gap: that rule only ever existed as a text
# instruction the model has to reliably re-apply every single time, and a real, reproduced live
# failure showed it didn't — "add price of 1500 with 12% off" (a genuinely complete, unambiguous
# instruction) got asked about anyway, then looped through several rounds of increasingly vague
# follow-up questions (see `run_ideation`'s own comment on the accumulation fix this pairs with). A
# plain regex match for an actually-stated number/currency/percent cannot misinterpret an
# instruction the way a model under real provider stress sometimes does — same "deterministic beats
# a prompt instruction for this exact class of check" reasoning `_is_bare_greeting` above already
# uses for greetings.
_STATED_VALUE_PATTERN = re.compile(
    r"\$\s?\d[\d,]*(\.\d+)?"                       # $1500, $1,500.00
    r"|\d[\d,]*(\.\d+)?\s?%"                        # 1500%, 12%
    r"|\bprice\s+(of|is|at)\s+\d"                   # price of 1500, price is 1500
    r"|\d[\d,]*(\.\d+)?\s*(dollars|usd|percent|off)\b",
    re.IGNORECASE,
)


def _check_price_stated(message: str) -> dict | None:
    """Returns a real `{"clear": True}` short-circuit (skipping the LLM clarity call entirely)
    when the message plainly states a real number/currency/percent value, or None to fall through
    to the LLM check for anything else — never itself claims "not clear"; it only ever short
    -circuits the ambiguous-number-claim case the prompt's own CRITICAL EXCEPTION already covers."""
    return {"clear": True} if _STATED_VALUE_PATTERN.search(message) else None


# Deliberately separate from `_SYSTEM_PROMPT` above, not a variant of it — that prompt's whole job
# is proposing options AND merging the brief into one clean paragraph; reusing it here for
# follow-ups would resurrect the exact lossy-summarization bug the `latest_element_storage_ref`
# bypass above exists to prevent. This prompt only ever judges ONE literal message in isolation and
# never touches the brief at all.
_FOLLOWUP_CLARITY_PROMPT = """You are reviewing ONE follow-up request against an existing
marketing asset. Your only job: decide whether it's clear enough to act on directly, or whether
something genuinely material is missing or ambiguous that would meaningfully change the result.

Default to NOT asking. Most follow-up requests — edits, tweaks, "recolor it red", "add a logo",
small stylistic choices ("calm music" is already a real, sufficient instruction — never ask it to
be more specific than that) — are clear enough to act on as-is, using sensible creative defaults
for anything genuinely left unspecified. Do NOT nitpick wording that is already a real instruction
just because it isn't maximally precise.

The one category that IS genuinely material, every time: the request implies a FACTUAL CLAIM
(a spec, a number, a price, a named feature) with NO SOURCE GIVEN ANYWHERE for it — neither in the
user's own message nor in the existing brief. This app's own generation guardrails elsewhere never
let a specialist invent a number/fact with no real data behind it — this is the same principle
applied one step earlier, before generation even starts. Concretely: "make a video of it racing on
track with voice over talk about its specs" — no actual spec numbers are given anywhere, so a
voiceover naming them would be invented from nothing; THIS is worth asking about (e.g. "I don't
have real specs on file for this — want me to use general/no specific claims, or do you have real
numbers I should use?").

CRITICAL EXCEPTION — if the user's OWN message directly states the actual value themselves (e.g.
"change the price text to $49.99", "the discount is 15% off"), that value IS the real source — the
user just gave it to you directly. NEVER ask about it in that case; that would be actively
insulting ("didn't you just tell me?") and is exactly the kind of number-losing regression this
whole check must never cause. Only ask when a claim is REFERENCED without its actual value ever
being stated by anyone ("talk about its specs" — no numbers given; "add the correct price" — no
number given), never when the value has already been given, by the user or the existing brief.

CONTEXT GUARDRAIL: If the user's message is a bare action (like "retry", "do it again", "start over") AND there is no clear subject or idea established in the existing context, you MUST mark `clear: false` and ask them what they actually want to create. Never let a completely context-free request proceed to generation where the downstream agents would be forced to guess or hallucinate a generic product.

Besides that, only ask when you genuinely could not proceed without guessing at
something important — e.g. the request is ambiguous between multiple real targets, or contradicts
something already established. Being asked something trivial on every single message is worse UX
than an occasional imperfect creative default — when in doubt on anything OTHER than an
unsupported factual claim or a completely missing subject, do NOT ask.

A real, live-found failure mode this check must specifically NOT reproduce (2026-09-22): a message
that describes a COMPLETELY DIFFERENT SUBJECT than the existing brief (e.g. the existing brief is
"a modern minimalist logo," and the new message asks to "generate 2 images of a ferrari") is NOT
genuinely ambiguous just because it doesn't fit the old brief — it's simply a new, unrelated
request, and the existing brief/asset is irrelevant to judging it. Do NOT try to reconcile a
clearly-different-subject request against the old brief, do NOT ask things like "did you mean add a
car to the logo, or change the subject entirely?" — a plainly different subject is never worth
asking about on THAT basis alone. Judge the new message's own clarity ON ITS OWN TERMS (is what
IT's asking for, by itself, missing something material?), never whether it fits the old brief.

If you do ask, propose 2 concrete, pickable options (not an open question) plus always allow free
text instead — same pattern as the rest of this app's ideation flow. Never ask more than one thing
at a time. Do not rewrite, merge, or summarize anything about the existing brief — that is not your
job here; only judge this one message.

BRAND & PRODUCT DNA (CRITICAL): If the user's message CONTAINS new information about their brand (e.g. "our brand colors are...") or their product (e.g. "the product is...", "never show XYZ"), you MUST extract these as distinct, actionable rules in `new_guardrails`. The same applies if they explicitly ask to update their Product DNA.

IMPORTANT: If the user explicitly indicates they want to cancel, stop, or pivot completely (e.g. "nevermind", "stop", "let's do something else", "cancel"), treat this as a clear new direction! Output `clear: true` so the system stops looping on the old question and follows their new direction.

Return ONLY JSON:
{
  "clear": true or false,
  "message": "a short line asking what's needed, only used when clear is false",
  "options": [{"id": "short_id", "label": "Bold label", "description": "one-line rationale"}],
  "new_guardrails": [{"source": "brand" or "product", "rule": "The explicit rule", "scope": "all"}] // only if the user provided new brand/product guidelines in their message
}
"""


async def _check_followup_clarity(brief: dict, user_message: str) -> dict | None:
    """Judges a single follow-up message for genuine ambiguity, without touching the brief —
    returns None (never blocks the turn) if the check itself fails for an infra reason, since a
    broken clarity check should never be worse than the old blind-bypass behavior it replaces.

    Only the real campaign IDEA text plus the specific existing/referenced ELEMENT (if any) are
    shown as context (2026-09-22 fix) — this used to dump the ENTIRE raw `brief` dict, including
    scratch fields that have nothing to do with judging THIS message's own clarity. A real,
    live-found failure mode: that much irrelevant noise measurably biased the model toward
    reconciling every new message against the old idea no matter how unrelated the new one actually
    was (e.g. a brief about "a minimalist logo" turned "generate 2 images of a ferrari" into an
    agonized "did you mean rebrand the logo as Ferrari?" instead of just recognizing a plainly
    different, unrelated subject).

    A SECOND real, live-found bug from that same fix, caught right after (2026-09-22): stripping
    the raw dict ALSO silently dropped `latest_element_type`/`latest_element_description` — the
    real signal that an element the user explicitly referenced in chat (e.g. "re: this audio",
    `referenced_element_id` — see `session_service.py`) is already available. Without it, a
    request like "create a video on this audio with same voiceover" looked exactly like a request
    referencing an audio file that was NEVER PROVIDED, and the check asked the user to upload one
    that was already sitting right there, referenced. Restored here specifically — deliberately NOT
    the whole raw dict again, just this one real, relevant fact, clearly separated from the (still
    noise-reducing) idea-only context above it."""
    llm = get_llm_provider()
    existing_idea = brief.get("idea") or "(nothing generated yet this session)"
    context_parts = [
        f"EXISTING campaign idea so far (context only — judge the NEW message below on its own "
        f"terms, not by how well it fits this):\n{existing_idea}"
    ]
    latest_ref = brief.get("latest_element_storage_ref")
    if latest_ref:
        existing_kind = brief.get("latest_element_type", "unknown kind")
        existing_desc = brief.get("latest_element_description") or "(no description recorded)"
        context_parts.append(
            f"An existing element is already available on the canvas — a real {existing_kind}, "
            f"described as: {existing_desc}\n"
            f"If the new message below refers to 'this image/video/audio' or similar, THIS is "
            f"what it means — that reference is already resolved, never ask the user to "
            f"upload/provide/link a file that's already right here."
        )
    context_parts.append(f"NEW message to evaluate for clarity:\n{user_message}")
    context = "\n\n".join(context_parts)
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_FOLLOWUP_CLARITY_PROMPT,
            messages=build_history_messages(brief, context),
            max_tokens=768,
            # Same reasoning as the main ideation call just above: a wrong "clear enough" judgment
            # either annoys the user with a pointless question or lets a genuinely ambiguous
            # request through to a real (sometimes paid) generation step — not worth the local
            # model's measurably weaker judgment on this exact kind of call.
            prefer_local=False,
        )
        parsed = extract_json(result.text)
    except Exception as exc:
        log.warning("followup_clarity_check_failed", extra={"_extra_error": str(exc)})
        return None

    raw_options = parsed.get("options")
    parsed["options"] = [
        opt for opt in (raw_options if isinstance(raw_options, list) else [])
        if isinstance(opt, dict) and opt.get("id") and opt.get("label")
    ]
    return parsed


@traceable(name="ideation_node")
async def run_ideation(state: GraphState) -> GraphState:
    brief = state.get("brief") or {}
    user_message = state.get("user_message") or ""

    # Resuming a paused "approve" mode video pipeline (Memory.md, Phase 4) — the user's message is
    # an approval/revision reply to an already-staged proposal, not new material to ideate on.
    # Skip straight through to the Orchestrator, which itself also short-circuits straight back to
    # the video pipeline (real, explicit state tracking is more reliable here than asking an LLM
    # to infer "we're mid-approval-flow" from a bare "approve" message with no other context).
    if brief.get("video_stage") in ("narrative_pending", "scene_pending"):
        state["route"] = None
        state["result"] = None
        return state

    if _is_bare_greeting(brief, user_message):
        state["result"] = {"message": _INTRO_MESSAGE, "options": [], "allow_free_text": True}
        log.info("ideation_turn", extra={"_extra_session_id": state.get("session_id"), "_extra_ready": False, "_extra_greeting": True})
        emit("ideation_completed", ready=False)
        return state

    # A real asset already exists for this session — every further message is the Orchestrator's
    # job to classify (full_image/full_video/direct_fix), not Ideation's to re-clarify. Real,
    # live-found reason (2026-09-21): routing every follow-up message through Ideation's "merge
    # into one clean paragraph" step, turn after turn, is lossy summarization compounding on
    # itself — a user's exact "15% off on 100k" survived one merge, then eroded into "a simple
    # discount overlay" after a few more option-picking rounds, so by the time Overlay Artist ran,
    # the real figures were already gone from the brief (Overlay Artist's own "never invent a
    # number" guardrail was working correctly — the number just wasn't there to find). The
    # Orchestrator's classifier already distinguishes "new generation" from "edit this" using the
    # raw message plus "is an existing element available" — Ideation's own full "merge into one
    # clean paragraph" step stays skipped here, unchanged from the fix above (`brief["idea"]` is
    # never rewritten by anything below). But per the user's explicit ask (2026-09-21): a genuinely
    # vague follow-up (e.g. "make a video of it racing on track...", no sense of tone/length/
    # emphasis) was ALSO skipping straight to generation with zero chance to ask anything, which is
    # a different, real problem from the numeric-erosion one this bypass was built to fix. A
    # separate, narrower `_check_followup_clarity` step below judges only whether THIS LITERAL
    # message is actionable — it never rewrites or summarizes the brief, so it can't reintroduce
    # the original bug — and defaults to NOT asking unless something genuinely material is missing.
    if brief.get("latest_element_storage_ref"):
        # Real, live-found Node Mode bug (2026-09-22, independent review): this branch used to
        # emit only `llm_delta` (via `_check_followup_clarity`'s own `on_delta`) with no matching
        # `_started`/`_completed` pair at all, so the node it created (`node="followup_clarity"`)
        # never got a real `endedAt` — its lane stayed open forever, `assignLanes` treated every
        # LATER node as overlapping it, and the whole turn rendered as one giant false "running in
        # parallel" wave. Wrapped in the same `ideation_started`/`ideation_completed` events the
        # main path below already emits (this check is conceptually still Ideation's job, just a
        # narrower one for follow-ups) — reusing the "ideation" node identity, not inventing a new
        # unbounded one.
        emit("ideation_started")
        # Real, live-found infinite-loop bug (2026-09-22): picking one of THIS check's own
        # clarifying options resubmits ONLY that option's short label text as the next turn's
        # `user_message` (session_service.py's `_last_option_labels` lookup) — the ORIGINAL
        # message that actually contained the real numbers/details is gone by the very next round.
        # A real, live-reproduced case: "add price of 1500 with 12% off" (a genuinely complete,
        # unambiguous instruction) still got asked about, and every subsequent option pick judged
        # only an increasingly generic fragment ("Add price and discount description") with the
        # real $1500/12% figures nowhere in it any more — looping forever, each round vaguer than
        # the last, the exact same numeric-erosion failure mode the comment above already names,
        # just re-emerging in THIS narrower check instead of the old full-brief-merge path it was
        # built to replace. Fixed by accumulating every round's real message onto the one being
        # judged (`_pending_clarification`, a normal persisted `brief` field — NOT a same-turn
        # scratch key — cleared the moment clarity is actually reached) instead of judging each
        # fragment in total isolation.
        pending = brief.get("_pending_clarification")
        
        # User Pivot: if the user explicitly cancels or pivots ("stop", "nevermind"), discard the old pending context.
        if pending and is_cancel(user_message):
            pending = None
            
        effective_message = f"{pending}\n\n{user_message}" if pending else user_message
        clarity = _check_price_stated(effective_message) or await _check_followup_clarity(brief, effective_message)
        
        if clarity and isinstance(clarity.get("new_guardrails"), list):
            state["new_guardrails"] = [g for g in clarity["new_guardrails"] if isinstance(g, dict)]

        if clarity is None or clarity.get("clear", True):
            state["route"] = None
            state["result"] = None
            # The FULL accumulated message (every round's real content, not just this turn's
            # fragment) becomes what the Orchestrator/specialists actually see from here on.
            state["user_message"] = effective_message
            if "_pending_clarification" in brief:
                brief = {k: v for k, v in brief.items() if k != "_pending_clarification"}
                state["brief"] = brief
            emit("ideation_completed", ready=True)
        else:
            brief = {**brief, "_pending_clarification": effective_message}
            state["brief"] = brief
            state["result"] = {
                "message": str(clarity.get("message") or "Could you say a bit more about what you'd like?"),
                "options": clarity.get("options") or [],
                "allow_free_text": True,
            }
            emit("ideation_completed", ready=False)
        return state

    llm = get_llm_provider()
    # `_recent_chat_history` excluded from this inline dump specifically — it's real conversation
    # history now, sent as its own proper messages (`build_history_messages` below), not something
    # that needs restating a second time inside this flattened-dict string too.
    brief_summary = {k: v for k, v in brief.items() if k != "_recent_chat_history"}
    context = f"Running brief so far:\n{brief_summary}\n\nLatest message from the user:\n{user_message}"

    emit("ideation_started")
    try:
        result = await llm.complete(
            tier=ModelTier.TIER_1,
            system=_SYSTEM_PROMPT,
            messages=build_history_messages(brief, context),
            # 512 was too tight in practice (Memory.md, Phase 1): several free-tier models spend
            # real tokens on internal reasoning before or interleaved with the visible JSON
            # content, and got cut off mid-response at the lower budget.
            max_tokens=1536,
            # Real, live-found reason (2026-09-21): the self-hosted TIER_1 model's judgment on
            # "is this brief ready" is measurably worse than Groq's — a side-by-side test on the
            # exact same input ("A red Ferrari") had the local model return `ready: false` and
            # silently drop "red Ferrari" from its own brief synthesis. Ideation gates the whole
            # conversation and any detail it drops never comes back, so it skips local-first
            # routing entirely rather than risk that — unlike the tool-calling Tier 1 specialists,
            # which tested fine locally and keep the default.
            prefer_local=False,
            # Real live "thinking" text, per the user's explicit ask (2026-09-21).
            on_delta=lambda delta: emit("llm_delta", node="ideation", text=delta),
        )
        parsed = extract_json(result.text)
    except Exception as exc:  # provider or parse failure — fail this turn clearly, don't crash the graph
        log.error("ideation_failed", extra={"_extra_error": str(exc)})
        raise SpecialistFailed("ideation", str(exc)) from exc

    # Defensive coercion, not trust — a free-tier model asked for {"idea": "..."} has, in real
    # testing (Memory.md, Phase 1), returned a bare string instead. Ported pattern from the
    # existing agentic_flow codebase's run_product_intelligence: never assume the model's JSON
    # matches the requested shape exactly, coerce it into something usable instead of crashing.
    raw_merged = parsed.get("merged_brief")
    if isinstance(raw_merged, dict):
        merged_brief = raw_merged
    elif isinstance(raw_merged, str) and raw_merged.strip():
        merged_brief = {"idea": raw_merged.strip()}
    else:
        merged_brief = {}
    
    # Extract new guardrails if provided (2026-09-24)
    new_guardrails = parsed.get("new_guardrails")
    if isinstance(new_guardrails, list):
        state["new_guardrails"] = [g for g in new_guardrails if isinstance(g, dict)]

    state["brief"] = {**brief, **merged_brief}

    raw_options = parsed.get("options")
    options = [
        opt for opt in (raw_options if isinstance(raw_options, list) else [])
        if isinstance(opt, dict) and opt.get("id") and opt.get("label")
    ]

    if parsed.get("ready") and merged_brief.get("idea"):
        state["route"] = None  # let the Orchestrator decide the route from the merged brief
        state["result"] = None
        # A real, one-off announcement of the mood/style direction just auto-chosen above (point 3
        # of _SYSTEM_PROMPT) — deliberately NOT part of merged_brief (which persists across turns in
        # session.brief), so it can't keep re-announcing an old choice on later, unrelated turns.
        # session_service.py strips this scratch key before persisting the brief, same treatment as
        # its other graph-input-only/turn-only fields.
        style_note = str(parsed.get("style_note") or "").strip()
        if style_note:
            state["brief"]["style_note"] = style_note
    else:
        state["result"] = {
            "message": str(parsed.get("message") or "Tell me more about what you have in mind."),
            "options": options,
            "allow_free_text": True,
        }

    log.info("ideation_turn", extra={"_extra_session_id": state.get("session_id"), "_extra_ready": parsed.get("ready")})
    emit("ideation_completed", ready=bool(parsed.get("ready")))
    return state
