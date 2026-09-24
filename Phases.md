# Phases — Agentic Marketing Studio POC

Each row below records what a phase actually used/built AT THE TIME — kept as real history, not
rewritten as things later changed. Real, current provider/tech choices have since diverged from
several rows here (image gen/editing moved to Qwen via Replicate 2026-09-24; the canvas was
custom-built, never tldraw, after Phase 4b's own plan) — see `Backend_Architecture.md`/
`Orchestration_Graph.md`/`Checkpoint.md`'s most recent entry for real current state.

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Scaffold** | The full 8-layer skeleton (`Architecture.md` section 2), empty tool/specialist registries wired but unpopulated, SQLite repositories, provider protocols defined (implementations stubbed), LangSmith connected to a "hello world" trace | The skeleton boots, one dummy trace appears in LangSmith, one dummy row round-trips through a repository |
| **1 — First vertical slice: Ideation + Visual Design Lead** | The Ideation node (propose-options pattern, `Architecture.md` section 1d) end-to-end into the Orchestrator's "full still image" route into Visual Design Lead's 4 specialists, using Pollinations (generation) + HuggingFace (editing) as the active image providers | A real end-to-end still image gets produced from a chat message, visible in LangSmith with real cost/latency numbers against the targets below |
| **2 — Remaining Leads + video** | Scene Lead, Narrative Lead, Motion Lead; fal.ai wired for video; the existing FFmpeg stitcher ported | A full video job runs end-to-end from a chat message |
| **3 — Knowledge layer + guardrail-first + compliance** | Real LlamaIndex indices for Brand/Product DNA (porting `run_product_intelligence` per `Rules.md` section 6), the guardrail-first binding for price/discount overlays, the trimmed Compliance set at the export gate | A generation demonstrably reflects a real brand kit, and a price overlay is provably bound from data, never free-generated |
| **4 — Canvas frontend** | **Done** (Memory.md: 4a scaffold+chat, 4b custom canvas, 4c SSE narration, 4d HITL gate UI — all verified live, sub-phases below). | A person can watch generation happen live, edit one tile without disturbing others, and approve through the same propose-options UI used in ideation |
| **5 — Tune against real numbers** | **Done** (Memory.md, 2026-09-21: real LangSmith key wired up, real traces gathered for full-image and video-narrative/scene turns, cross-referenced with real token/cost logs). No numeric targets were ever written down anywhere in this project's docs to compare against — this pass established the first real baseline instead. | Documented actual numbers (table in Memory.md); decision: no model-tiering/parallelization/provider change needed — reasoning is $0 and sub-second, the only real latency variable is external Pollinations image-gen rate-limiting |

## Phase 4 sub-phases — frontend build-out

Broken down so each sub-phase is independently completable and verifiable against the already-built
backend, rather than one large "build the frontend" step with no checkpoint in between.

| Sub-phase | Scope | Exit criteria |
|---|---|---|
| **4a — Scaffold + Chat/Ideation** | Next.js (App Router, TypeScript, Tailwind) project under `poc/frontend/`; a single chat view wired to the real `POST /api/v1/sessions` and `POST /api/v1/sessions/{id}/turns` endpoints — renders the propose-options-plus-free-text pattern (`Architecture.md` section 1d) as pickable cards + a text input, exactly matching `IdeationPrompt`'s real shape. No canvas, no SSE, no HITL UI yet. | A real conversation runs end-to-end in the browser against the real backend: a message starts a session, options render as real clickable cards, a pick or free-text reply continues the same session, and a "completed" turn (image/video actually generated) is shown honestly even with no canvas to display it in yet. |
| **4b — Canvas (tldraw)** | A tldraw-based canvas view fetching `GET /api/v1/canvas/{session_id}`, rendering each real `CanvasElement` (image/video) as a tile. Wires the three intervention paths already built on the backend: direct edit, targeted regenerate, comment. | Generated elements appear as real tiles on a real canvas; a regenerate/comment/direct-edit against a real element produces a visibly different tile, backed by the real API, not mocked data. |
| **4c — SSE live narration** | Opens `GET /api/v1/sessions/{id}/events` around each turn and renders the real event stream (ideation/routing/Lead/specialist/tool-call events — `core/events.py`'s real shape) as live chat-bubble narration rather than a bare loading spinner. | A person watching the browser sees generation happen step-by-step in real time, matching the real backend trace, not a synthetic progress bar. |
| **4d — HITL approval UI** | Renders the real per-stage pipeline gates (narrative/scene/motion-spend) and per-edit staging (approve-edit/reject-edit) using the same card+free-text component already built in 4a — approval is a rendering job, not a new interaction pattern. | A person can approve, revise, or reject at each real gate from the UI, and the real backend state (video_stage, pending_storage_ref) changes accordingly — confirmed by refetching, not assumed from the click alone. |

Per this doc's own rule below: don't start 4b until 4a's exit criteria are actually verified in a
real browser, and so on down the list — a sub-phase marked done on assumption defeats the point of
splitting it out at all.

## Proposed starting targets

Baseline only, meant to be revised once Phase 1's real LangSmith data comes in:

| What | Target | Why this number |
|---|---|---|
| One ideation round-trip (a proposed option + wait for reply) | Under ~5s | Uses a Tier 1 (small/fast) model — should feel conversational, not laggy |
| One image tile, full generation | Under ~15-20s | Free-tier/keyless hosted image endpoints are slower than a paid API |
| One targeted edit (Composition Artist / inpainting) | Under ~15s | Should feel close to instant compared to a full regeneration |
| A full "from scratch" image job (Visual Design Lead's 4-specialist sequence) | Under ~60-90s | Multiple sequential model calls; parallelizing what's safe keeps this bounded |
| A full video job (Narrative → Scene → Motion leads) | Under ~5 minutes | Video generation is inherently the slowest step, more so on free-tier endpoints |
| Cost per demo run | Effectively $0, tracked, not assumed | Every provider is free-tier or bounded trial credit — LangSmith turns "should be free" into a verified fact |

## Notes

- Do not start a phase before the previous one's exit criteria are actually verified — a phase
  marked done on assumption defeats the point of tracking phases at all.
- Update `Memory.md` continuously during a phase; write a `Checkpoint.md` entry at the end of each
  phase (or any natural pause point).
