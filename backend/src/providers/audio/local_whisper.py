"""
THE ONLY file that imports faster-whisper/the local speech-emotion classifier — local, open-weights
audio-understanding models, run in-process rather than called over the network.

Added 2026-09-22, per an explicit user ask: this app previously had ZERO ability to understand
audio it did NOT itself generate. Generated audio (Sound Designer's `text_to_speech` tool) already
carries its own real script in `voiceover_line` metadata — no transcription is ever needed there,
that would just be re-deriving text the app already has verbatim. A user-UPLOADED audio file is the
real gap: no script, no metadata, genuinely unknown content.

A real, live-found gap in the FIRST version of this file, caught by the user before it ever
shipped: plain speech-to-text only recovers the literal WORDS — it throws away pitch, energy,
pacing, and emotion entirely, which is most of what "tone/pace/mood/vibe" actually means for audio.
"I'm so excited" read flat and sarcastic transcribes identically to the same line read genuinely
excited; a transcript alone can never tell those apart. This file now does two real, separate
passes over the actual audio, not just one:
  1. faster-whisper — real transcription, PLUS real pacing computed from its own segment
     timestamps (words / minutes spoken) — a measured number, not a guess.
  2. A small local wav2vec2 speech-emotion classifier (`superb/wav2vec2-base-superb-er`) — a real
     acoustic judgment of tone (angry/happy/neutral/sad), trained on how something was actually
     SAID, not on the words alone.

Same "local model" reasoning as `local_kokoro.py`'s own choice for TTS: genuinely free forever, no
external call, no per-request cost. Both models together are still well under 1GB, downloaded once
from the HF Hub and cached, and run comfortably on CPU for short marketing voiceover/audio clips.
"""
from __future__ import annotations

import io
import time

from faster_whisper import WhisperModel
from transformers import pipeline as hf_pipeline

from ...core.middleware.logging import get_logger
from .base import TranscriptionProvider, TranscriptionResult

log = get_logger(__name__)

_whisper_model: WhisperModel | None = None
_emotion_classifier = None


def _get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        log.info("whisper_model_loading")
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    return _whisper_model


def _get_emotion_classifier():
    global _emotion_classifier
    if _emotion_classifier is None:
        log.info("speech_emotion_model_loading")
        _emotion_classifier = hf_pipeline(
            "audio-classification", model="superb/wav2vec2-base-superb-er", device=-1
        )
    return _emotion_classifier


def _pace_label(wpm: float | None) -> str | None:
    # Real, ordinary conversational-English speech-rate bands (~110-150 wpm is typical
    # "moderate" pace) — same three-way vocabulary `pacing_editor.md` already outputs, so a
    # referenced audio's measured pace can be compared/used directly, not remapped later.
    if wpm is None:
        return None
    if wpm < 110:
        return "slow_deliberate"
    if wpm > 160:
        return "fast_cut"
    return "moderate"


class LocalWhisperTranscriptionProvider(TranscriptionProvider):
    async def transcribe(self, *, audio_bytes: bytes, mime_type: str) -> TranscriptionResult:
        whisper = _get_whisper_model()
        start = time.monotonic()
        segments, info = whisper.transcribe(io.BytesIO(audio_bytes), beam_size=5)
        segments = list(segments)  # faster-whisper's generator is single-use; needed twice below
        text = " ".join(s.text.strip() for s in segments).strip()

        pace_wpm: float | None = None
        if segments and text:
            spoken_seconds = segments[-1].end - segments[0].start
            word_count = len(text.split())
            if spoken_seconds > 0:
                pace_wpm = round(word_count / (spoken_seconds / 60), 1)

        mood: str | None = None
        mood_confidence: float | None = None
        try:
            classifier = _get_emotion_classifier()
            # transformers' audio-classification pipeline accepts a raw bytes-like input directly
            # (it decodes via ffmpeg/soundfile internally) — no separate resampling step needed.
            predictions = classifier(audio_bytes)
            if predictions:
                top = max(predictions, key=lambda p: p["score"])
                mood, mood_confidence = top["label"], round(float(top["score"]), 3)
        except Exception as exc:
            # An honest degrade, never a crash: the transcript + pace are still real and usable
            # even if the emotion model itself fails on a particular file (format quirks, etc.).
            log.warning("speech_emotion_failed", extra={"_extra_error": str(exc)})

        log.info(
            "whisper_transcribed",
            extra={
                "_extra_ms": round((time.monotonic() - start) * 1000),
                "_extra_language": info.language,
                "_extra_chars": len(text),
                "_extra_pace_wpm": pace_wpm,
                "_extra_mood": mood,
            },
        )
        return TranscriptionResult(
            text=text,
            language=info.language,
            pace_wpm=pace_wpm,
            pace_label=_pace_label(pace_wpm),
            mood=mood,
            mood_confidence=mood_confidence,
            provider_name="local_whisper+wav2vec2_ser",
        )


def get_transcription_provider() -> TranscriptionProvider:
    """The active TranscriptionProvider — callers depend on this, never the concrete class
    directly, same pattern as `local_kokoro.py`'s own `get_audio_provider`."""
    return LocalWhisperTranscriptionProvider()
