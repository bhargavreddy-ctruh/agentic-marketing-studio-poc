"""
llm.py — Anthropic Messages API client with tool-use + vision.

Reads LLM_PROVIDER / LLM_API_KEY / LLM_MODEL from agent_core config.
"""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER, LLM_WORKSPACE_ID
from .logger import get_logger

log = get_logger(__name__)


class LLMError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


# Back-compat alias used by campaign_studio
CampaignLLMError = LLMError


@dataclass
class LLMResponse:
    """Normalized Anthropic messages response."""

    stop_reason: str | None
    content: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        parts = [
            b.get("text", "")
            for b in self.content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [
            b
            for b in self.content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]


def _base_url() -> str:
    if LLM_BASE_URL:
        return LLM_BASE_URL.rstrip("/")
    if LLM_PROVIDER == "anthropic":
        return "https://api.anthropic.com"
    return "https://api.anthropic.com"


def require_llm_key() -> None:
    if not LLM_API_KEY:
        raise LLMError(
            "LLM API_KEY is not configured. Set PROVIDER, API_KEY, and MODEL "
            "in services/config.ini [LLM]",
            500,
        )


class AnthropicClient:
    """Async Anthropic Messages API client."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        workspace_id: str | None = None,
    ):
        self.api_key = (api_key or LLM_API_KEY).strip()
        self.model = (model or LLM_MODEL).strip() or "claude-haiku-4-5-20251001"
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.workspace_id = (workspace_id if workspace_id is not None else LLM_WORKSPACE_ID).strip()

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | str | None = None,
        max_tokens: int = 4096,
        retries: int = 2,
    ) -> LLMResponse:
        if not self.api_key:
            raise LLMError("LLM_API_KEY missing", 500)

        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        # Identity-linked / multi-workspace keys require this header.
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            if isinstance(tool_choice, str):
                body["tool_choice"] = {"type": tool_choice}
            else:
                body["tool_choice"] = tool_choice

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(url, headers=headers, json=body)

                if resp.status_code == 429:
                    wait = 1.5 * (attempt + 1)
                    log.warning("Anthropic rate limited; retry in %.1fs", wait)
                    await asyncio.sleep(wait)
                    raise LLMError("Anthropic rate limited", 429)

                if resp.status_code >= 500:
                    wait = 1.0 * (attempt + 1)
                    log.warning("Anthropic HTTP %d; retry in %.1fs", resp.status_code, wait)
                    await asyncio.sleep(wait)
                    raise LLMError(
                        f"Anthropic HTTP {resp.status_code}: {resp.text[:200]}",
                        502,
                    )

                if resp.status_code >= 400:
                    raise LLMError(
                        f"Anthropic HTTP {resp.status_code}: {resp.text[:400]}",
                        resp.status_code if resp.status_code < 500 else 502,
                    )

                data = resp.json()
                return LLMResponse(
                    stop_reason=data.get("stop_reason"),
                    content=list(data.get("content") or []),
                    raw=data,
                )
            except LLMError as exc:
                last_err = exc
                if exc.status_code not in (429, 502) or attempt >= retries:
                    raise
            except httpx.TimeoutException as exc:
                last_err = LLMError(f"Anthropic timeout: {exc}", 504)
                log.warning("Anthropic timeout attempt %d", attempt + 1)
            except Exception as exc:
                last_err = LLMError(str(exc), 502)
                log.warning("Anthropic unexpected error attempt %d: %s", attempt + 1, exc)

        raise last_err or LLMError("Anthropic call failed")


def get_llm_client() -> AnthropicClient:
    require_llm_key()
    if LLM_PROVIDER not in {"anthropic", ""}:
        log.warning(
            "LLM_PROVIDER=%s — agent_core currently uses Anthropic Messages API",
            LLM_PROVIDER,
        )
    return AnthropicClient()


def _sniff_image_mime(data_b64: str) -> str | None:
    """Detect image MIME from magic bytes; ignore declared type mismatches."""
    try:
        # Only decode a small prefix — enough for PNG/JPEG/WEBP/GIF signatures.
        head = base64.b64decode(data_b64[:64] + "==", validate=False)
    except Exception:
        return None
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 6 and head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def resolve_image_mime(mime_type: str | None, data_b64: str | None = None) -> str:
    """Return a MIME type that matches the image bytes when sniffing succeeds."""
    sniffed = _sniff_image_mime(data_b64) if data_b64 else None
    mime = sniffed or mime_type or "image/jpeg"
    if mime == "image/jpg":
        mime = "image/jpeg"
    return mime


def image_block(mime_type: str, data_b64: str) -> dict[str, Any]:
    """Build an Anthropic image content block from base64."""
    mime = resolve_image_mime(mime_type, data_b64)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime,
            "data": data_b64,
        },
    }


def tool_result_block(
    tool_use_id: str,
    content: Any,
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    """
    Build a tool_result content block.

    If `content` is a dict with `__images__` (list of image blocks), the tool_result
    content becomes a multimodal list: text summary + images (Anthropic format).
    """
    images: list[dict[str, Any]] = []
    payload = content
    if isinstance(content, dict) and "__images__" in content:
        images = list(content.get("__images__") or [])
        payload = {k: v for k, v in content.items() if k != "__images__"}

    if images:
        text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
        result_content: Any = [{"type": "text", "text": text}, *images]
    elif isinstance(content, (dict, list)):
        result_content = json.dumps(content)
    else:
        result_content = str(content)

    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": result_content,
    }
    if is_error:
        block["is_error"] = True
    return block
