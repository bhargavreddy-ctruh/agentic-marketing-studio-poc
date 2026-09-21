"""
Sniff an image's real MIME type from its magic bytes rather than trusting a declared/assumed type.

Direct port of the existing agentic_flow codebase's resolve_image_mime() (Rules.md section 6) —
more relevant now than before, since generation is moving to less battle-tested free-tier
endpoints (Pollinations, HuggingFace) than the previously-used Gemini API.
"""
from __future__ import annotations


def sniff_image_mime(data: bytes, declared: str | None = None) -> str:
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    mime = declared or "image/jpeg"
    return "image/jpeg" if mime == "image/jpg" else mime
