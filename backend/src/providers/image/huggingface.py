"""
THE ONLY file that talks to HuggingFace's Inference Providers — used for image editing/inpainting.

Rewritten (Memory.md, Phase 3) after HuggingFace deprecated the old direct REST endpoint this file
previously called (`api-inference.huggingface.co/models/<id>`) entirely — confirmed via DNS lookup
(the hostname no longer resolves at all) and their own current docs, which now route everything
through `router.huggingface.co` and explicitly recommend their `huggingface_hub` client library
for non-chat tasks like image-to-image rather than hand-rolled REST against a moving target. Uses
the official `huggingface_hub.InferenceClient.image_to_image()` — the one vendor SDK call this
file makes, matching every other provider file's "one vendor SDK, one file" rule.
"""
from __future__ import annotations

import io
import time

from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError
from PIL import Image

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import ImageEditProvider, ImageResult

log = get_logger(__name__)


class HuggingFaceImageEditProvider(ImageEditProvider):
    def __init__(self, api_token: str | None = None, model: str | None = None):
        self._api_token = api_token or settings.huggingface_api_token
        self._model = model or settings.huggingface_image_edit_model

    async def edit(self, *, image_bytes: bytes, mime_type: str, instruction: str) -> ImageResult:
        if not self._api_token:
            raise ProviderUnavailable("huggingface", "HUGGINGFACE_API_TOKEN is not set")

        client = InferenceClient(api_key=self._api_token)
        start = time.monotonic()
        try:
            result_image: Image.Image = client.image_to_image(
                image=image_bytes, prompt=instruction, model=self._model
            )
        except HfHubHTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 503:
                # HF's "model is loading, try again shortly" — a real, common free-tier behavior.
                raise ProviderUnavailable("huggingface", "model is loading, retry shortly") from exc
            raise ProviderUnavailable("huggingface", f"HTTP {status}: {exc}") from exc
        except Exception as exc:  # the SDK raises its own exception types for network/timeout — none escape this file
            raise ProviderUnavailable("huggingface", str(exc)) from exc

        buf = io.BytesIO()
        result_image.save(buf, format="PNG")
        content = buf.getvalue()

        log.info(
            "huggingface_edit",
            extra={
                "_extra_model": self._model,
                "_extra_bytes": len(content),
                "_extra_ms": round((time.monotonic() - start) * 1000, 1),
            },
        )
        return ImageResult(image_bytes=content, mime_type="image/png", provider_name="huggingface")


_singleton: HuggingFaceImageEditProvider | None = None


def get_image_edit_provider() -> ImageEditProvider:
    """The active ImageEditProvider — callers depend on this, never the concrete class directly
    (Rules.md section 1: Dependency Inversion)."""
    global _singleton
    if _singleton is None:
        _singleton = HuggingFaceImageEditProvider()
    return _singleton


def reset_image_edit_provider() -> None:
    """See llm/router.py's reset_llm_provider() docstring — same pattern, for
    huggingface_api_token."""
    global _singleton
    _singleton = None
