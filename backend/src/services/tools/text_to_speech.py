"""
Text-to-Speech tool — Sound Designer's real voiceover capability (Architecture.md section 1b).

Converts a written line into real, audible speech via `providers/audio/`'s active provider. See
`providers/audio/local_kokoro.py` for why a local model was chosen over HuggingFace/Groq/
Pollinations (none of those turned out to be genuinely free for this task, live-checked).
"""
from __future__ import annotations

from ...core.local_storage import save_asset
from ...providers.audio.local_kokoro import get_audio_provider
from .base import Tool, ToolResult
from .registry import register_tool


@register_tool("text_to_speech")
class TextToSpeechTool(Tool):
    name = "text_to_speech"
    description = "Synthesizes a line of text into real, audible speech audio."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "voice": {"type": "string", "description": "Requested voice, if the active provider supports one"},
        },
        "required": ["text"],
    }

    async def run(self, args: dict, context: dict | None = None) -> ToolResult:
        text = str(args.get("text") or "").strip()
        voice = args.get("voice")
        if not text:
            return ToolResult(ok=False, data={}, error="text is required")

        provider = get_audio_provider()
        result = await provider.synthesize(text=text, voice=voice)

        new_ref = save_asset(
            result.audio_bytes,
            result.mime_type,
            metadata={"tts_text": text, "provider": result.provider_name},
        )
        return ToolResult(ok=True, data={"storage_ref": new_ref, "mime_type": result.mime_type})
