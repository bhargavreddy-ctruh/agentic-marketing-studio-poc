"""
Central, env-driven configuration.

Rules.md section 2: no hardcoded model IDs, API base URLs, or tier assignments outside this file
(or the registries that read from it). Every provider key below is Optional — Phase 0 must boot
cleanly with none of them set, per the "scaffold with placeholders" decision. A provider that
needs a missing key raises ProviderUnavailable at call time, not at import time.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Observability ---
    langsmith_api_key: str | None = None
    langsmith_project: str = "agentic-marketing-studio-poc"
    langsmith_tracing: bool = True

    # --- Compliance QA gate (compliance_gate.py) — runs automatically after every generation/edit
    # (session_service.py). A real, extra round of LLM/vision calls per generation; set to false
    # for faster/cheaper local iteration. When off, an element's compliance_status is set straight
    # to "disabled" rather than the default "running" — an honest "not checked", never a fabricated
    # "passed".
    compliance_qa_enabled: bool = True

    # --- Multi-generation (graph.py, 2026-09-22) — a single request can ask for several DISTINCT
    # images/videos (e.g. "2 images, one pink one green"). Genuinely INDEPENDENT variants (neither
    # needs the other's real result to exist first) run concurrently by default for speed; a
    # DEPENDENT sequence (e.g. "generate a car, then a video based on that exact image") always
    # runs sequentially regardless of this flag — that's a real correctness requirement, not a
    # preference, since a dependent step literally cannot start before the one it depends on
    # produces a real result. Set to false to force EVERY multi-generation request fully
    # sequential, independent variants included — real cost/predictability control, especially for
    # video: each independent video variant is its own real, separate paid Replicate render, so
    # "parallel" here directly means "spend on N renders at once" rather than one at a time.
    multi_generation_parallel_enabled: bool = True

    # --- Live "LLM thinking" streaming (_openai_compatible.py, per the user's explicit ask,
    # 2026-09-21) — real streamed text fragments, emitted live via the existing SSE event bus as
    # they arrive from the provider, instead of only ever seeing a node's final result once it's
    # done. A real, meaningfully more complex code path (has to reassemble streamed tool-calls
    # correctly, not just plain text) — set to false to fall back to the original, simpler,
    # already-proven single-shot request/response call for faster/cheaper local iteration or if
    # streaming ever misbehaves. The final LLMResult is identical either way; this only changes
    # whether partial text is visible before a node finishes.
    stream_llm_thinking_enabled: bool = True

    # --- Canvas element versioning (versioning_service.py) — real, live-found user ask
    # (2026-09-24): "every generated element should be displayed on canvas as individual element,
    # detach the element versioning for now." Default ON keeps the original, still-fully-built
    # behavior (a targeted regenerate/comment/direct-edit/chat direct_fix versions the SAME
    # element, undo/redo moves through its history) — set OFF to make every one of those instead
    # create a brand-new, independent canvas element every time, never touching an existing one.
    # A real, disclosed simplification while off: "approve" mode's staging (`apply_or_stage`)
    # doesn't compose with "always create new" without a "pending new element" concept that
    # doesn't exist, so a new element is applied immediately either way, regardless of
    # approval_mode, while this is off. Nothing about `undo`/`redo`/`/versions` is removed — only
    # `apply_or_stage`'s own behavior changes; flipping this back on is the whole rollback.
    canvas_versioning_enabled: bool = False

    # --- Reasoning (Gemini) ---
    # Gemini serves as the primary fallback when Groq rate limits.
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_model_tier_1: str = "gemini-3.5-flash-lite"
    gemini_model_tier_2: str = "gemini-3.8-flash"
    gemini_model_tier_3: str = "gemini-3.8-flash"
    gemini_vision_model: str = "gemini-3.8-flash"

    # Groq — a second LLM gateway, used only as a whole-provider fallback (router.py) when
    # OpenRouter itself is unavailable. Added after real testing found OpenRouter's free tier has
    # a hard 50-requests/day cap (Memory.md, Phase 2) that no per-model retry/fallback within
    # OpenRouter can route around. No credit card required per Groq's own signup flow ("on_demand"
    # service tier, confirmed via a real call). Model IDs below were live-verified against Groq's
    # real /v1/models endpoint plus a real completion call each — the first guess
    # (llama-3.1-8b-instant / llama-3.3-70b-versatile, from third-party blog posts) turned out to
    # be stale/removed from the live catalog entirely, the same "don't trust an unverified model
    # name" lesson OpenRouter's Tier 1 already taught once. Re-verify before relying on these long
    # term; Groq's catalog changes over time same as OpenRouter's.
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model_tier_1: str = "llama-3.1-8b-instant"
    groq_model_tier_2: str | None = "llama-3.1-70b-versatile"
    groq_model_tier_3: str | None = "llama-3.1-70b-versatile"
    # A dedicated vision-capable model, deliberately separate from the Tier 1/2/3 system — vision
    # is a CAPABILITY question ("can this model see an image at all"), not a "how smart" tier
    # choice, and none of the Tier models above support image input. Confirmed live and free
    # (Memory.md, Phase 4): a real image correctly described, including spotting a watermark.
    groq_vision_model: str = "llama-3.2-90b-vision-preview"



    # --- Text-to-speech (local, not a remote provider) ---
    # Confirmed live (Memory.md): HuggingFace's own free `hf-inference` serverless tier hosts NO
    # text-to-speech models at all — every provider HF lists for this task (fal-ai, replicate,
    # groq, etc.) is a paid third-party route. Groq TTS and Pollinations "openai-audio" were also
    # checked and are real but not free (Groq's TTS model has no confirmed free tier; Pollinations'
    # own docs say speech is billed per character, unlike its free image endpoint). Running
    # Kokoro-82M locally (`providers/audio/local_kokoro.py`) is genuinely free forever — the same
    # "local model" pattern already used for LlamaIndex's embeddings, just heavier.
    kokoro_lang_code: str = "a"  # American English
    kokoro_voice: str = "af_heart"

    # --- Video generation ---
    falai_api_key: str | None = None
    falai_base_url: str = "https://queue.fal.run"
    falai_model: str = "fal-ai/kling-video/v1.6/standard/image-to-video"
    falai_poll_interval_seconds: float = 4.0
    falai_max_poll_attempts: int = 60  # ~4 minutes, matching the ~5 minute video job target

    # Replicate — added as a second video provider option (Memory.md, Phase 2): fal.ai's account
    # balance was found exhausted during real testing, so this exists as an alternative to try
    # once a real key is added, not because fal.ai's provider code was wrong.
    # bytedance/seedance-2.0-fast (2026-09-25, replacing prunaai/p-video — see
    # providers/video/replicate.py's own docstring): real duration/resolution/aspect_ratio input
    # fields, confirmed live against the model's own schema, not a text-to-video guess.
    replicate_api_token: str | None = None
    replicate_model: str = "bytedance/seedance-2.0-fast"

    # --- Product/Brand crawler (2026-09-28) — Ollama revived, scoped ONLY to crawler extraction
    # (services/knowledge/{brand,product}_dna_service.py's crawl_*_from_url methods). General LLM
    # reasoning stays exactly Groq -> Replicate (router.py), never touches Ollama. Not DB-managed
    # (settings_service.py) since these are plain local config (where the container lives), not
    # a secret to rotate.
    ollama_base_url: str = "http://ollama:11434/v1"
    ollama_model: str = "gemma2:2b"
    # firecrawl_api_key IS DB-managed (settings_service.py's _STR_SETTINGS_KEYS) like every other
    # provider key in this app — this default is only the env-var/local fallback.
    firecrawl_api_key: str | None = None

    # --- Persistence ---
    # No default (2026-09-30, per an explicit user ask: "no database fallback, only .env" — this
    # app's only real connection is Postgres/Supabase; a silent local-SQLite default here
    # contradicted that same rule already applied to storage/settings elsewhere). Missing
    # DATABASE_URL now fails loudly with a clear pydantic ValidationError at process startup,
    # never a silent switch to a different database.
    database_url: str

    # --- Runtime settings overrides (services/settings/settings_service.py, 2026-09-28) — how
    # often the background poll loop re-reads the `app_settings` table for hand-edited rows (the
    # user edits these directly in Supabase's dashboard; there's no in-app write path to react to
    # sooner). Lower = faster pickup, more idle DB polling; 30s is a reasonable default for a
    # POC-scale app where "no restart needed" matters more than sub-second propagation.
    settings_poll_interval_seconds: float = 30.0

    # --- App ---
    log_level: str = "INFO"
    correlation_header: str = "X-Correlation-ID"
    # Comma-separated allowed origins for the Next.js frontend (Phase 4a) — config-driven per
    # Rules.md section 2, not hardcoded into main.py itself.
    frontend_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Auth (Tasks_Workflows.md #1) — signs the session cookie's HMAC (core/security.py).
    # A real dev-only default so the app still boots with zero setup, per the project's own
    # "scaffold with placeholders" pattern — MUST be overridden via .env for anything beyond a
    # single developer's local machine, since anyone who has this value can forge a valid login.
    auth_secret_key: str = "dev-only-insecure-secret-change-in-.env"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days


settings = Settings()
