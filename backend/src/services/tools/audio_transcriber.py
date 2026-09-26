"""
Audio Transcriber tool — real understanding of an EXISTING audio element (words, measured pace,
and acoustic mood/tone), via `providers/audio/local_whisper.py`'s active provider. The inverse of
`text_to_speech.py`: that tool turns text INTO audio for something this app generates itself; this
tool turns an audio file — most importantly one this app did NOT generate, a user upload with no
recorded script anywhere — back into real, structured understanding a specialist can act on.
"""
from __future__ import annotations

from typing import ClassVar

from ...core.local_storage import load_asset
from ...providers.audio.local_whisper import get_transcription_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("audio_transcriber")
class AudioTranscriberTool(Tool):
    name = "audio_transcriber"
    description = (
        "Transcribes an existing audio element's real spoken words, measured speaking pace, and "
        "acoustic mood/tone. Use this whenever a request references an existing audio clip and "
        "its actual content/vibe isn't already known from context — most importantly a "
        "user-uploaded audio file, which has no script recorded anywhere else."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "storage_ref": {"type": "string", "description": "storage_ref of the existing audio element to transcribe"},
        },
        "required": ["storage_ref"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        raw_ref = args.get("storage_ref") or args.get("reference_storage_ref") or args.get("audio_storage_ref") or args.get("video_storage_ref") or ""
        storage_ref = str(raw_ref).strip()
        if not storage_ref:
            return ToolResult(ok=False, data={}, error="storage_ref is required")

        loaded = load_asset(storage_ref)
        if loaded is None:
            return ToolResult(ok=False, data={}, error=f"no asset found for storage_ref {storage_ref}")
        audio_bytes, mime_type = loaded

        provider = get_transcription_provider()
        result = await provider.transcribe(audio_bytes=audio_bytes, mime_type=mime_type)
        return ToolResult(ok=True, data={
            "text": result.text,
            "language": result.language,
            "pace_wpm": result.pace_wpm,
            "pace_label": result.pace_label,
            "mood": result.mood,
            "mood_confidence": result.mood_confidence,
        })
