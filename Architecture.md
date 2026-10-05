# Architecture — Agentic Marketing Studio POC

**This section (1-1d) is the ORIGINAL design, transcribed from the source PDF — kept for
historical reference.** Real, current-state docs, written from the live code rather than the
plan, live in [Backend_Architecture.md](Backend_Architecture.md) and
[Orchestration_Graph.md](Orchestration_Graph.md) — read those for what actually runs today. The
single biggest divergence: the Orchestrator below describes 3 original routes; the real system has
grown a 4th (`full_audio`, 2026-09-22) and a 5th, `dynamic` (an LLM-planned multi-step specialist
sequence that bypasses the fixed Lead structure entirely, for requests that don't cleanly fit any
fixed route) — see `Orchestration_Graph.md` §2.

## 1. App flow and architecture (original design)

```
User message
  -> Ideation node (LangGraph; proposes 2-4 concrete options + free-text input; loops until ready)
  -> Orchestrator — exactly three routes:
       1. Full still image  -> Visual Design Lead (+ Scene Lead if a background/scene is needed)
       2. Full video        -> Narrative Lead -> Scene Lead -> Motion Lead
       3. Single-element fix -> direct specialist call, bypassing its Lead
  -> Lead subgraph runs its specialists in sequence (see 1a below), parallelizing independent
     steps (e.g. Environment Designer + Palette Strategist when neither depends on the other)
  -> each specialist calls its Tool(s) -> Tool calls a Provider:
       - Groq (primary) / OpenRouter (fallback) for all reasoning (tiered by task, see 3).
         Native multimodal image context is supported, directly passing base64 images to vision-capable models (e.g., Gemini) instead of text descriptions.
         A self-hosted Ollama model is TIER_1's own preferred-first option, plus a genuine
         last-resort fallback to that same local model for ANY tier if Groq AND every free
         OpenRouter model in that tier are unavailable at once — quality priority is unchanged in
         the normal case; this only degrades a request to the smaller local model instead of
         failing it outright during a real free-tier outage (see Memory.md). A local Laya decision
         model (2026-09-23) additionally runs in shadow mode alongside real classification, and as
         a real fallback specialist-suggestion source (with explicit user approval) when both
         remote LLM gateways are down.
       - Image generation AND editing both run on `alibaba/qwen-image-3` via Replicate (switched
         2026-09-24 from Pollinations/Cloudflare FLUX.2 — see Backend_Architecture.md §10.1 for
         why), with real support for aspect_ratio, a reference image (edit AND image-to-image
         generation), negative_prompt, seed, and prompt expansion; fal.ai free-trial credits for
         video (Gemini/Vertex/Bedrock kept registered as a future option once quota resets, not
         active today)
       - Price/discount/product facts bound directly from Product DNA — never free-generated
         (the guardrail-first pattern)
  -> result written to Canvas (SQLite, via real repositories — see 4 on portability) -> pushed to
     frontend via SSE
  -> single interrupt gate before Export, running the trimmed Compliance set, presented through
     the SAME propose-options-plus-free-text pattern as ideation
  -> Export/Publish
```

Every node run, tool call, and provider call is traced in LangSmith — this is the actual evidence
for "good, cheap, fast," not a claim. (Real, disclosed limit: the configured LangSmith API key has
never had read access this project — traces write successfully but can't be read back for
debugging via the API; browser access to the LangSmith UI still works.)

### 1a. Leads and specialists (exact, per the source PDF, nothing further trimmed)

| Lead | Specialists (in order) | Full-job trigger |
|---|---|---|
| Visual Design Lead | Reference Curator → Palette Strategist → Illustrator → Composition Artist | New still image from scratch |
| Scene Lead | Environment Designer → Prop Stylist → Lighting Designer | New scene/background |
| Narrative Lead | Shot Planner → Script Writer → Pacing Editor | New video storyboard |
| Motion Lead | Camera Director → Video Editor/Cutter → Sound Designer → Overlay Artist | New finished video |

Plus the trimmed Compliance set at the export gate: Brand Consistency Checker, Visual Fidelity
Checker, Format/Technical QA.

### 1b. Tools (19, per the PDF, minus what it explicitly excludes)

Base Image Generator, Image Editor/Inpainter, Base Video Generator, Video Extender, Video Stitcher,
Faithful Upscaler, Creative Upscaler, Text-to-Speech, Voice Cloner, Text Preserve/Overlay, Color
Palette Extractor, Discount/Claims Calculator, Format Converter, Brand Kit Lookup, Product Lookup,
Web/Trend Search, Asset/Mood Board Search, Router, Export/Publish.

(3D Generator and Commerce/Storefront connectors are excluded by the source PDF itself.)

**Real, disclosed addition beyond the original 19 (2026-10-01):** two more image tools, both
gated on a specific context signal rather than used by default — `photorealistic_image_generator`
(Google's Nano Banana 2 Lite via Replicate, used only when a request genuinely calls for a
photorealistic result) and `high_resolution_image_generator` (Google's Nano Banana 2 via Replicate,
used only when the user explicitly asks for 2K/4K/high resolution — includes real
`google_search`/`image_search` web-grounding flags, off by default). Both are additional choices on
the Illustrator's own tool list (see `Orchestration_Graph.md`), not a replacement for
`base_image_generator`.

### 1c. The Human-in-the-Loop Canvas

- Canvas State holds each generated element individually addressable, each pointing back to the
  specialist that produced it.
- Three intervention paths:
  1. **Direct edit** — manual crop/recolor/retouch, no model call.
  2. **Targeted regenerate** — select one element, ask for a redo; invokes exactly the one
     specialist that produced it, never the whole Lead.
  3. **Comment** — a note on an element that the Orchestrator resolves into a scoped instruction
     against that one specialist.
- No copy-related canvas elements (no caption/headline fields) — Copy Lead isn't in scope.
- **Per-element undo/redo** — regenerating one element must never risk a neighboring one. This is a
  modeling constraint (independent versioning per element), not just a UI feature.
- The compliance gate is the one mandatory interrupt before export.

### 1d. The Ideation interaction pattern (confirmed against a real Luma Agents reference)

Every agent-to-human touchpoint in this POC — ideation *and* the compliance gate — uses the same
shape: a short plain-language line stating what's needed, a **titled card with 2-4 pickable
options** (bold label + one-line description each), and **always a free-text input alongside it**.
Never a bare form, never raw JSON. Drag-and-drop assets onto the canvas is part of the same
conversational flow, not a separate upload form.

### 1e. Real additions beyond the original design (see Backend_Architecture.md for full detail)

- **Real auth** (2026-09-22) — every session/brand/product row is owned by a real user
  (`hashlib.pbkdf2_hmac` passwords, a signed stateless cookie). "Auth: None" in §3's tech-stack
  table below is the ORIGINAL plan, not current reality.
- **Real, persisted chat history** (`ChatTurnModel`) — every turn's user text, "thinking" text, and
  final response, not just the session's current `brief`.
- **Real Guardrails system** — per-session rules (brand/product-derived + user-added), rendered as
  XML into every specialist's system prompt, editable from the chat UI.
- **Product DNA and Mood Board**, parallel to Brand DNA — real onboarding/search, with frontend UI
  added 2026-09-23/24.
- **Persistent semantic chat/brand/product/mood-board memory** (2026-09-24) — LlamaIndex indices
  now persist to disk (`var/knowledge_index/`) and survive a restart, not just live in memory.
- **Guardrails can now actually be linked to real Brand/Product DNA** (2026-09-24) — a real linking
  mechanism (`PUT .../guardrails/link-profiles`) that never existed before; approval mode
  (auto/approve) can be changed mid-conversation, not just at session creation; canvas element
  versioning can be fully detached via a config flag (every edit becomes its own element instead of
  a version of an existing one); real LLM provider retries/fallbacks are now visible live in chat
  and Node Mode, not just server-logged.
- **Brand/Product DNA scraping made genuinely real-time, concurrent, and per-user selectable**
  (2026-10-05) — brand and product can now scrape simultaneously with independent live progress
  bars (a missing `set_current_session()` call had silently dropped every crawl SSE event before
  this); a per-session lock fixes a real lost-update race between concurrent crawls; a Brand DNA
  dropdown lets a user pick from their own previously-scraped brands (strictly per-user). See
  Memory.md's "Brand/Product DNA Scraping" entry for the full root-cause list.
- **Canvas list responses and specialist prompts are now both bounded, not bloated** (2026-10-05)
  — canvas element descriptions are capped in the list response (the full vision-model text is
  still used for real grounding server-side); every specialist now gets a short, curated "what's
  available this turn" summary instead of the session's entire raw `brief` dict; `product_lookup`
  is now a MANDATORY call (not advisory wording) for every product-facing specialist, not just
  illustrator/overlay_artist. Chat history also now lazy-loads (most recent turns first, older ones
  paged in on scroll) instead of fetching a session's entire turn history up front. See Memory.md's
  "Canvas/Assets Load Performance" and "Chat History Lazy-Load" entries for full detail.

## 2. Folder and file structure

**This tree is the ORIGINAL plan.** The real, current tree has grown real `guardrails`/`brand`/
`product`/`mood_board`/`auth` API routers, real `knowledge/` services beyond Brand DNA, `leads/`
files for all four Leads, ~16 specialist prompt files and ~18 tools (not just the ones sampled
below), and a frontend with no `tldraw` dependency at all (a custom-built canvas engine,
`components/canvas/CanvasEngine.tsx` — tldraw was deliberately avoided over licensing concerns; see
Backend_Architecture.md/Orchestration_Graph.md for the real, current shape). Kept below for the
original intent/rationale, not as a literal current directory listing.

```
poc/
  PRD.md
  Architecture.md
  Rules.md
  Phases.md
  Memory.md
  Checkpoint.md
  Backend_Architecture.md      # real, current-state backend description (2026-09-22+)
  Orchestration_Graph.md       # real, current-state routing/graph description (2026-09-22+)
  backend/
    src/
      core/
        middleware/
          correlation.py        # correlation ID, attached to every log line
          logging.py            # structured JSON logging setup
          error_handler.py      # typed exception -> HTTP response
        config.py                # env-driven config, no secrets hardcoded
        exceptions.py            # the typed exception taxonomy (see Rules.md)
      api/
        v1/
          sessions/routes.py     # start a session, post a chat/ideation turn
          canvas/routes.py       # fetch canvas state, regenerate, comment, approve/export
      schemas/
        sessions/{requests,responses}.py
        canvas/{requests,responses}.py
      services/
        ideation/ideation_service.py
        orchestration/
          orchestrator.py        # the 3-route decision (1)
          graph.py                # LangGraph graph assembly, wires nodes together
        leads/
          visual_design_lead.py
          scene_lead.py
          narrative_lead.py
          motion_lead.py          # each: a declarative specialist sequence, not hand-coded logic
        specialists/
          registry.py             # loads declarative specialist specs at startup
          prompts/                 # one prompt file per specialist (data, not code)
            illustrator.md
            composition_artist.md
            ... (one per specialist, 1a)
        tools/
          base.py                  # the Tool protocol
          registry.py               # TOOL_REGISTRY + @register_tool decorator
          base_image_generator.py
          image_editor.py
          ... (one file per tool, 1b)
        compliance/
          brand_consistency_checker.py
          visual_fidelity_checker.py
          format_technical_qa.py
        knowledge/
          brand_dna_service.py
          product_dna_service.py    # near-port of the existing agentic_flow run_product_intelligence
          guardrail_synthesizer.py
      repositories/
        base.py                    # Repository protocols — the swap point for local -> online
        session_repository.py
        canvas_repository.py
        brand_repository.py
        product_repository.py
        sqlite/                    # current implementation of the protocols above
          sqlite_session_repository.py
          sqlite_canvas_repository.py
          ...
      providers/
        llm/
          base.py                  # LLMProvider protocol
          openrouter.py             # the only file importing the OpenRouter client
        image/
          base.py
          pollinations.py           # active
          huggingface.py            # active (editing)
          gemini.py                 # registered, inactive by config (quota exhausted)
          vertex.py
          bedrock.py
        video/
          base.py
          falai.py                  # active
          gemini.py                 # registered, inactive by config
        knowledge/
          base.py
          llamaindex_provider.py
          embeddings_local.py       # local sentence-transformers wrapper
        observability/
          langsmith.py
      mappers/
        canvas_mapper.py
        session_mapper.py
        brand_mapper.py
      models/
        session.py
        canvas_element.py
        brand_profile.py
        product_profile.py
        generation_job.py
        tool_call_log.py
    tests/
      unit/            # services, no DB, no network
      integration/      # repositories, real SQLite
    pyproject.toml
  frontend/
    app/                # Next.js app router
    components/
      canvas/            # tldraw wrapper + tile rendering
      chat/              # the propose-options-plus-free-text UI (1d)
    lib/
      sse-client.ts
    package.json
```

## 3. Tech stack

**This table is the ORIGINAL plan — several rows have since changed for real, disclosed reasons.
Current reality (2026-09-24): image generation AND editing both run on Qwen via Replicate (not
Pollinations/Cloudflare/HuggingFace); persistence is real SQLAlchemy repositories, not a LangGraph
checkpointer; real auth exists; the canvas is a custom-built engine, not tldraw.** See
`Backend_Architecture.md`/`Orchestration_Graph.md` for the real current shape; the table below is
kept for the original rationale.

| Layer | Choice | Why |
|---|---|---|
| Backend framework | FastAPI | Matches the team's `genai_build` engineering guide's expected shape; proven in the existing `agentic_flow` codebase |
| Agent orchestration | LangGraph core primitives (tool-calling + conditional routing), hand-built | LangGraph's own maintainers recommend this over the `langgraph-supervisor` wrapper, which is effectively in maintenance mode |
| Reasoning models | OpenRouter, one gateway, free-tier models routed by tier | One API key, swap which model fills which tier via config; genuinely $0 |
| Model tiering | Tier 1 = small free model (Gemma-class) for simple/mechanical specialists; Tier 2/3 = stronger free-tier models, picked from OpenRouter's free catalog at build time | Matches the Tier 0-3 system from the full architecture document |
| Knowledge / RAG | LlamaIndex — Property Graph Index (Brand DNA), Document Summary Index (Product DNA), real from day one | Confirmed decision — not a static-config fallback |
| Embeddings | A local, free, open-source embedding model (small `sentence-transformers` model) | Zero cost, zero rate limits, no new API key |
| Image generation | Pollinations.ai (free, keyless) | Gemini/Veo's free-tier quota on this account is exhausted |
| Image editing / inpainting | Cloudflare Workers AI FLUX.2 [klein] 4B (free Neuron allocation), falling back to HuggingFace Inference API (free tier) | Cloudflare added 2026-09-21 after HuggingFace's fal-ai sub-provider hit a real `402 Payment Required` (credits exhausted) |
| Video generation | fal.ai free trial credits (+ existing FFmpeg stitcher, reused unchanged) | Bounded but real, enough to prove the agents work |
| Observability | LangSmith | Native LangGraph integration, free tier, per-node cost/latency traces |
| Persistence | SQLite via LangGraph's own checkpointer | Zero infra now — see 4 for the portability requirement |
| Generated file storage | Local disk, same pattern as the existing `clip_store.py` | Proven pattern, zero cost |
| Auth | None, for this POC | Single-user internal validation; nothing here blocks adding real auth later |
| Live updates | SSE from FastAPI, porting the existing `emit()` event pattern | Simplest thing that shows live narration + canvas tiles filling in |
| Frontend | Next.js + React + TypeScript + Tailwind CSS, canvas built on tldraw (open-source) | Luma-like tile canvas without hand-building pan/zoom/selection mechanics |

## 4. Portability: local → online, nothing rigid

This is a first-class requirement, not an afterthought — enforced structurally, not just by intent:

- **Every persistence access goes through a `Repository` protocol** (`repositories/base.py`).
  Today's implementation is SQLite; swapping to Postgres/Supabase/any hosted DB later means writing
  one new class per repository that satisfies the same protocol — zero changes to any service.
- **Every external generation/reasoning call goes through a `Provider` protocol.** Turning a
  provider on, off, or swapping it (Pollinations → Gemini once quota resets; free OpenRouter model →
  a paid one later) is a config change plus, at most, one new file — never a rewire of a specialist
  or the orchestrator.
- **Every tool and specialist is registry-based, not hard-wired** — adding, removing, or disabling
  one is a registration change, not a graph-surgery operation.
- **File storage is a small interface too** — local disk today, swappable to any object store later
  the same way.

The concrete test for "is this actually modular": grep for the vendor/library name (`sqlite3`,
`pollinations`, `openrouter`) — it should only ever appear inside its own one provider or
repository file, never scattered through services.
