"""
THE ONLY file that talks to Cloudflare Workers AI — FLUX.2 [klein] 4B
(`@cf/black-forest-labs/flux-2-klein-4b`), a real free-allocation alternative to HuggingFace's
fal-ai sub-provider after that one started returning 402 Payment Required (a real, disclosed gap
this fills, not a hypothetical).

NOT currently wired into `image_editor.py` (2026-09-24) — that tool now calls
`replicate_provider.py`'s Qwen edit path instead, a real, disclosed, user-chosen tradeoff (full
`aspect_ratio`/`negative_prompt`/`seed` support and no forced 512x512 downscale, at a real per-edit
cost this free allocation didn't have). Left in place, not deleted, as a real available fallback if
Qwen/Replicate is ever down — but nothing currently calls `get_cloudflare_flux_provider()`.

Cloudflare's Workers AI REST API always wraps a model's own output in one response envelope,
regardless of model: `{"result": {...}, "success": bool, "errors": [...], "messages": [...]}`.
This model's own result shape is `{"image": "<base64 PNG/JPEG>"}` per Cloudflare's docs. This file
also tolerates the response coming back as a raw image byte stream instead of that JSON envelope
(checked via the response `Content-Type`) — Workers AI has changed response shape for other image
models before, and this hasn't been exercised against every account/plan configuration.

Per Cloudflare's own docs for this specific model, the request is always multipart/form-data (even
prompt-only), and up to four reference/edit images go in fields named `input_image_0`..`input_image_3`,
each required to be smaller than 512x512 — enforced here by downscaling before upload rather than
letting the API reject an oversized image.
"""
from __future__ import annotations

import base64
import io
import time

import httpx
from PIL import Image

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import ImageEditProvider, ImageGenProvider, ImageResult

log = get_logger(__name__)

# Cloudflare's own constraint is images "smaller than 512x512" — strictly less than, not
# less-than-or-equal. Target 511, not 512 (independent review, 2026-09-22): PIL's `.thumbnail()`
# only guarantees fitting WITHIN a box, so an input already exactly 512 on its long edge would
# have been left at exactly 512 — one pixel over the real limit — under the previous constant.
_MAX_INPUT_DIMENSION = 511


def _downscale_to_limit(image_bytes: bytes) -> bytes:
    """FLUX.2 [klein]'s input images must be smaller than 512x512 (Cloudflare's own constraint,
    not a guess) — resized here so a normal canvas-sized asset doesn't get rejected outright."""
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((_MAX_INPUT_DIMENSION, _MAX_INPUT_DIMENSION), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class CloudflareFluxProvider(ImageGenProvider, ImageEditProvider):
    def __init__(
        self,
        account_id: str | None = None,
        api_token: str | None = None,
        model: str | None = None,
    ):
        self._account_id = account_id or settings.cloudflare_account_id
        self._api_token = api_token or settings.cloudflare_api_token
        self._model = model or settings.cloudflare_flux_model

    def _require_credentials(self) -> None:
        if not self._account_id or not self._api_token:
            raise ProviderUnavailable(
                "cloudflare_flux", "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN are not set"
            )

    async def _run(
        self, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None
    ) -> tuple[bytes, str]:
        self._require_credentials()
        url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/{self._model}"
        headers = {"Authorization": f"Bearer {self._api_token}"}

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                # Verified directly (independent review, 2026-09-22 — the original comment here
                # was wrong and has been corrected): httpx only encodes multipart/form-data when
                # `files` contains at least one real entry — `files={}` and `files=None` both
                # silently fall back to a plain urlencoded body, identically. Cloudflare's own docs
                # say this model always requires multipart, even for a prompt-only call with no
                # real images attached, so a genuine empty-content dummy field forces the right
                # encoding when there's nothing real to attach (edit() always has a real file).
                effective_files = files or {"__multipart_marker__": ("", b"", "application/octet-stream")}
                resp = await client.post(url, headers=headers, data=fields, files=effective_files)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("cloudflare_flux", str(exc)) from exc

        if resp.status_code >= 400:
            raise ProviderUnavailable("cloudflare_flux", f"HTTP {resp.status_code}: {resp.text[:300]}")

        content_type = resp.headers.get("content-type", "")
        if content_type.startswith("image/"):
            image_bytes = resp.content
            # The real content-type Cloudflare actually sent, not assumed — this branch can
            # legitimately be JPEG, not just PNG (independent review, 2026-09-22: the old code
            # hardcoded "image/png" for this path regardless of what was actually returned).
            mime_type = content_type.split(";")[0].strip() or "image/png"
        else:
            payload = resp.json()
            if not payload.get("success", True):
                raise ProviderUnavailable("cloudflare_flux", f"errors={payload.get('errors')}")
            b64 = (payload.get("result") or {}).get("image")
            if not b64:
                raise ProviderUnavailable("cloudflare_flux", f"no image in response: {payload}")
            image_bytes = base64.b64decode(b64)
            # Cloudflare's own docs just say "base64 PNG/JPEG" with no field naming which —
            # a cheap real magic-byte check beats assuming PNG unconditionally.
            mime_type = "image/jpeg" if image_bytes[:2] == b"\xff\xd8" else "image/png"

        log.info(
            "cloudflare_flux_call",
            extra={
                "_extra_model": self._model,
                "_extra_bytes": len(image_bytes),
                "_extra_mime_type": mime_type,
                "_extra_ms": round((time.monotonic() - start) * 1000, 1),
            },
        )
        return image_bytes, mime_type

    async def generate(self, *, prompt: str, aspect_ratio: str = "1:1") -> ImageResult:
        # `aspect_ratio` is accepted (to satisfy the shared ImageGenProvider Protocol) but not
        # forwarded — Cloudflare's docs for this model never confirmed a real width/height/
        # aspect_ratio request field, unlike the input-image constraints, which ARE documented.
        # Sending an unverified field risked a silent no-op or a real rejection; honest to drop it
        # than guess. Nothing in this codebase currently calls this class at all (2026-09-24 —
        # `image_editor.py` moved to Qwen; see this file's module docstring), so this has no live
        # impact either way — flagged rather than fixed further without real API confirmation.
        image_bytes, mime_type = await self._run(fields={"prompt": prompt}, files=None)
        return ImageResult(image_bytes=image_bytes, mime_type=mime_type, provider_name="cloudflare_flux")

    async def edit(self, *, image_bytes: bytes, mime_type: str, instruction: str) -> ImageResult:
        resized = _downscale_to_limit(image_bytes)
        files = {"input_image_0": ("input.png", resized, "image/png")}
        result_bytes, result_mime_type = await self._run(fields={"prompt": instruction}, files=files)
        return ImageResult(image_bytes=result_bytes, mime_type=result_mime_type, provider_name="cloudflare_flux")


_singleton: CloudflareFluxProvider | None = None


def get_cloudflare_flux_provider() -> CloudflareFluxProvider:
    """The concrete Cloudflare provider — callers that need a specific fallback order (e.g.
    image_editor.py preferring this over HuggingFace) import this directly; callers that don't
    care which provider runs should still depend on the `ImageEditProvider`/`ImageGenProvider`
    Protocols (Rules.md section 1: Dependency Inversion)."""
    global _singleton
    if _singleton is None:
        _singleton = CloudflareFluxProvider()
    return _singleton
