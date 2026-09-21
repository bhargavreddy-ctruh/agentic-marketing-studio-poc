"""
THE ONLY file that imports Kokoro — a local, open-weights TTS model, run in-process rather than
called over the network.

Live-checked, in order, before landing on this (Memory.md): HuggingFace's own free `hf-inference`
serverless tier hosts NO text-to-speech models at all (confirmed by querying its models API
directly — every provider it lists for the task, fal-ai/replicate/groq/etc., is a paid third-party
route, the same kind of surprise that already hit `image_editor`'s `fal-ai` sub-provider once).
Groq's TTS model (`canopylabs/orpheus-v1-english`) has no confirmed free tier either. Pollinations'
own docs say speech is billed per character, unlike its free image endpoint. Running Kokoro-82M
locally sidesteps all of that: genuinely free forever, no external call, no per-request cost or
rate limit — the same "local model" choice this project already made for LlamaIndex's embeddings
(`providers/knowledge/embeddings_local.py`), just a heavier one-time model download (~300MB from
the HF Hub, plus a small spacy English model `misaki` pulls in for its G2P — both fetched once and
cached, not on every call).

Not free of dependencies, honestly: this needs `torch` (already installed, for the embedding
model) plus the `kokoro`/`soundfile` packages. No `espeak-ng` system binary is required for
English — confirmed live; `misaki`'s English grapheme-to-phoneme path uses spacy, not espeak.
"""
from __future__ import annotations

import io
import time

import soundfile as sf
import torch
from kokoro import KPipeline

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import AudioGenProvider, AudioResult

log = get_logger(__name__)

_SAMPLE_RATE = 24000

# Kokoro-82M's real American-English ('a') voice pack ids — the only ones that actually exist on
# the model's HF repo. A real bug found live: the tool schema lets a specialist's LLM freely name
# any `voice` string, and a plausible-but-nonexistent guess (e.g. "default") 404s against HF's
# voice-pack download rather than failing gracefully. Never trust an unvalidated model guess here —
# same "coerce or drop, never crash on it" rule already applied to a free-tier model's JSON shape
# elsewhere in this project (Memory.md, Phase 1's ideation_service.py fix).
_KNOWN_VOICES = frozenset({
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore", "af_nicole",
    "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa",
})


class LocalKokoroTtsProvider(AudioGenProvider):
    def __init__(self, lang_code: str | None = None, voice: str | None = None):
        self._lang_code = lang_code or settings.kokoro_lang_code
        self._voice = voice or settings.kokoro_voice
        # Loaded lazily, once — the model weights are only fetched/deserialized on first real use,
        # same reasoning as the local embedding model's own lazy singleton.
        self._pipeline: KPipeline | None = None

    def _get_pipeline(self) -> KPipeline:
        if self._pipeline is None:
            self._pipeline = KPipeline(lang_code=self._lang_code)
        return self._pipeline

    async def synthesize(self, *, text: str, voice: str | None = None) -> AudioResult:
        if not text.strip():
            raise ProviderUnavailable("kokoro", "text is empty")

        resolved_voice = voice if voice in _KNOWN_VOICES else self._voice
        if voice and voice not in _KNOWN_VOICES:
            log.warning(
                "kokoro_unknown_voice_requested",
                extra={"_extra_requested": voice, "_extra_fallback": resolved_voice},
            )

        start = time.monotonic()
        try:
            pipeline = self._get_pipeline()
            chunks = [audio for _graphemes, _phonemes, audio in pipeline(text, voice=resolved_voice)]
        except Exception as exc:  # a local model call — no vendor HTTP errors, but keep the same
            # "nothing escapes this file" rule every other provider follows.
            raise ProviderUnavailable("kokoro", str(exc)) from exc

        if not chunks:
            raise ProviderUnavailable("kokoro", "produced no audio")
        waveform = torch.cat(chunks) if len(chunks) > 1 else chunks[0]

        buf = io.BytesIO()
        sf.write(buf, waveform.numpy(), _SAMPLE_RATE, format="WAV")
        audio_bytes = buf.getvalue()

        log.info(
            "kokoro_tts",
            extra={
                "_extra_voice": resolved_voice,
                "_extra_bytes": len(audio_bytes),
                "_extra_ms": round((time.monotonic() - start) * 1000, 1),
            },
        )
        return AudioResult(audio_bytes=audio_bytes, mime_type="audio/wav", provider_name="kokoro")


_singleton: LocalKokoroTtsProvider | None = None


def get_audio_provider() -> AudioGenProvider:
    """The active AudioGenProvider — callers depend on this, never the concrete class directly
    (Rules.md section 1: Dependency Inversion). A singleton because the model is expensive to
    load, same reasoning as `providers/knowledge/llamaindex_provider.py`'s embedding singleton."""
    global _singleton
    if _singleton is None:
        _singleton = LocalKokoroTtsProvider()
    return _singleton
