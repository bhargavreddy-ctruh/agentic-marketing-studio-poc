"""The AudioGenProvider contract every text-to-speech provider must satisfy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AudioResult:
    """Our own result type — never the vendor's raw response object."""

    audio_bytes: bytes
    mime_type: str
    provider_name: str


class AudioGenProvider(Protocol):
    async def synthesize(self, *, text: str, voice: str | None = None) -> AudioResult:
        """
        `voice` is a real, requested parameter when supplied, but whether a given provider can
        honor a specific named voice depends on that provider's real API — check the ACTIVE
        provider's own file for what actually happens with it, same disclosure rule as
        `VideoGenProvider.generate()`'s aspect_ratio/resolution parameters.
        """
        ...


@dataclass
class TranscriptionResult:
    """Our own result type — never the vendor's raw response object.

    Deliberately more than just `text` (2026-09-22, a real gap caught before this ever shipped —
    plain speech-to-text throws away everything that ISN'T the literal words: pitch, energy,
    pacing, emotion). `pace_label`/`pace_wpm` are computed from real Whisper segment timestamps
    (genuinely measured, not guessed), and `mood`/`mood_confidence` come from a real acoustic
    speech-emotion classifier run on the actual audio — never inferred from word choice alone,
    which a transcript-only pass could never tell apart from tone (e.g. "I'm so excited" said
    flat/sarcastic vs. genuinely excited reads identically as TEXT but very differently as AUDIO)."""

    text: str
    language: str | None
    pace_wpm: float | None
    pace_label: str | None  # "slow_deliberate" | "moderate" | "fast_cut" — same vocabulary pacing_editor.md already uses
    mood: str | None
    mood_confidence: float | None
    provider_name: str


class TranscriptionProvider(Protocol):
    async def transcribe(self, *, audio_bytes: bytes, mime_type: str) -> TranscriptionResult:
        """Real speech-to-text — the inverse of `AudioGenProvider.synthesize`. Exists specifically
        for audio this app did NOT itself generate (a user upload), where there is no recorded
        script/voiceover_line anywhere to fall back on — see `local_whisper.py`."""
        ...
