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

    # --- Reasoning (OpenRouter) ---
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Tier -> comma-separated model id list, tried in order with fallback to the next on
    # persistent failure — the same provider-fallback-chain pattern already proven in the
    # existing agentic_flow codebase's image/video providers, applied here because real testing
    # showed free-tier OpenRouter models genuinely do get transiently congested (Memory.md,
    # Phase 1). Every model below was verified live against OpenRouter's actual /models endpoint
    # plus a real completion call — not a guess. Re-verify before relying on these long-term,
    # since OpenRouter's free catalog changes over time.
    model_tier_1: str = "google/gemma-4-26b-a4b-it:free,liquid/lfm-2.5-2.6b:free"
    model_tier_2: str | None = "nex-agi/nex-n2.5-pro:free,nvidia/nemotron-3-super-120b-a12b:free"
    model_tier_3: str | None = (
        "nvidia/nemotron-3-ultra-550b-a55b:free,deepseek/deepseek-v4-flash-0731:free"
    )

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
    groq_model_tier_1: str = "openai/gpt-oss-20b"
    groq_model_tier_2: str | None = "openai/gpt-oss-120b"
    groq_model_tier_3: str | None = "openai/gpt-oss-120b"
    # A dedicated vision-capable model, deliberately separate from the Tier 1/2/3 system — vision
    # is a CAPABILITY question ("can this model see an image at all"), not a "how smart" tier
    # choice, and none of the Tier models above support image input. Confirmed live and free
    # (Memory.md, Phase 4): a real image correctly described, including spotting a watermark.
    groq_vision_model: str = "qwen/qwen3.8-27b"

    # Self-hosted (Ollama) — Tier 1's new primary (router.py), ahead of Groq/OpenRouter: genuinely
    # $0 and rate-limit-free since it runs on this machine, matching ModelTier.TIER_1's own
    # "small/fast model (Gemma-class)" docstring in providers/llm/base.py. Ollama exposes the same
    # OpenAI-compatible /chat/completions shape Groq/OpenRouter already use, so it reuses
    # _openai_compatible.py rather than a new HTTP client.
    #
    # NOT actually Gemma: `gemma3:4b` was tried first (per the original ask) and confirmed via a
    # real call to have zero tool-calling support (`ollama show gemma3:4b` lists only
    # `completion`/`vision` capabilities; a live call with `tools` attached returned "does not
    # support tools"). Two of Tier 1's four specialists (Reference Curator, Palette Strategist)
    # genuinely call tools (`brand_kit_lookup`, `web_trend_search`), so a model that can't tool-call
    # can't actually serve as Tier 1's primary. `qwen2.5:3b` was swapped in instead — confirmed via
    # `ollama show` (`tools` capability listed) and a real tool-call round-trip that correctly
    # returned a `brand_kit_lookup` call with real arguments.
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_api_key: str = "ollama"  # unauthenticated local server; never checked by Ollama
    local_llm_model_tier_1: str = "qwen2.5:7b"

    # --- Image generation ---
    pollinations_base_url: str = "https://image.pollinations.ai"
    huggingface_api_token: str | None = None
    huggingface_base_url: str = "https://api-inference.huggingface.co"
    huggingface_image_edit_model: str = "black-forest-labs/FLUX.1-Kontext-dev"

    # --- Image editing (Cloudflare Workers AI, FLUX.2 [klein] 4B) — a real second option for
    # image_editor.py, added because HuggingFace's fal-ai sub-provider was hitting a real 402
    # Payment Required (Memory.md's disclosed gap). Cloudflare gives a 10,000-Neuron/day free
    # allocation shared across all Workers AI models on the account; this model costs ~5.37
    # Neurons per 512x512 input tile and ~26.05 per 512x512 output tile (Cloudflare's own pricing
    # page) — genuinely free at this project's volume, not "free" in the vaguer sense. Preferred
    # over HuggingFace when both are configured (image_editor.py), since it doesn't share HF's
    # exhausted fal-ai credits.
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_flux_model: str = "@cf/black-forest-labs/flux-2-klein-4b"

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
    # prunaai/p-video specifically chosen per the user's own example: a genuinely light, fast
    # model ("generates a video in under 10 seconds") — ideal for testing, not a guess.
    replicate_api_token: str | None = None
    replicate_model: str = "prunaai/p-video"

    # --- Legacy providers (registered, inactive by default — see Rules.md section 6) ---
    gemini_api_key: str | None = None
    gemini_image_active: bool = False
    gemini_video_active: bool = False

    # --- Persistence ---
    database_url: str = "sqlite+aiosqlite:///./poc.db"

    # --- App ---
    log_level: str = "INFO"
    correlation_header: str = "X-Correlation-ID"
    # Comma-separated allowed origins for the Next.js frontend (Phase 4a) — config-driven per
    # Rules.md section 2, not hardcoded into main.py itself.
    frontend_origins: str = "http://localhost:3000"

    # --- Auth (Tasks_Workflows.md #1) — signs the session cookie's HMAC (core/security.py).
    # A real dev-only default so the app still boots with zero setup, per the project's own
    # "scaffold with placeholders" pattern — MUST be overridden via .env for anything beyond a
    # single developer's local machine, since anyone who has this value can forge a valid login.
    auth_secret_key: str = "dev-only-insecure-secret-change-in-.env"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days


settings = Settings()
