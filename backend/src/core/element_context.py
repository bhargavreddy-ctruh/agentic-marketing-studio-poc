"""
Shared "give the model the real image WITH its JSON metadata, never JSON alone" helper (Fix 8 of
the 2026-09-26 image/video quality investigation).

Real, live-found gap: only one of three places that reason about "what is this referenced
element" (`graph.py`'s `_dynamic_executor_node`) ever attached the real image alongside an
element's text description — `_direct_fix_node` (the route used for "fix/edit this specific
element") and `orchestrator.py`'s routing classifier (the very first "what is this turn about"
decision) both reasoned from JSON/text alone, with no image ever attached. A text description can
be stale, wrong, or (per `core/element_descriptions.py`) an outright placeholder — the same class
of anti-pattern a real multimodal assistant (Claude/Gemini/GPT chat) never has, since it can
always see the actual picture. This is the one shared implementation all three call sites use.

Cost discipline: images are downscaled to a small thumbnail before being attached — vision token
cost scales with resolution, and a thumbnail is entirely sufficient for "what is this / is this
the same element," as opposed to the full-resolution asset a GENERATION call needs. Callers should
only pass the specific element(s) actually relevant to their call, never a large candidate list.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from .element_descriptions import NO_DESCRIPTION_SENTINEL
from .local_storage import load_asset
from .middleware.logging import get_logger

log = get_logger(__name__)

_THUMBNAIL_MAX_DIM = 512


def _downscale_thumbnail(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Resizes to at most `_THUMBNAIL_MAX_DIM` on the long edge. Any decode/processing failure
    falls back to the original bytes/mime rather than dropping the image entirely — full-res at
    higher cost beats no image at all."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((_THUMBNAIL_MAX_DIM, _THUMBNAIL_MAX_DIM))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=80)
            return out.getvalue(), "image/jpeg"
    except Exception as exc:  # best-effort downscale, never blocks reasoning
        log.warning("element_thumbnail_downscale_failed", extra={"_extra_error": str(exc)})
        return image_bytes, mime_type


async def get_verified_image_description(element: dict[str, Any]) -> str | None:
    """Real, live-found gap (2026-09-30): `_direct_fix_node`/`_dynamic_executor_node` reasoned
    about a referenced image only from its RECORDED description (a generation prompt — what was
    ASKED for, not necessarily what the model actually delivered), never a fresh look. Attaching a
    real `image_url` content block directly (`build_element_context_blocks` below) turned out to
    be dead weight for this: every real specialist call routes through `groq.py`, which passes
    `strip_images=True` unconditionally — the image block was always silently discarded before the
    request ever reached the model, with no error to reveal it. This calls `complete_with_vision`
    (Groq's real vision model, falling back to Replicate's Gemini 2.5 Flash) ONCE, the same
    "vision-once, thread the text into a normal specialist call" pattern
    `session_service.py`'s `_describe_uploaded_image` already proved out for uploads — reuses the
    element's already-known Cloudinary url (`public_url()`), no byte re-download needed. Persists
    the result onto the element's own `metadata_json["verified_description"]` (via the canvas
    repository) so this is a real, one-time fix per element, not recomputed on every later turn
    that references the same image again. Returns None (caller keeps its own existing fallback)
    for a non-image element, one with no real storage_ref, or on any vision failure — never blocks
    the turn over this."""
    if element.get("element_type") != "image":
        return None
    ref = element.get("storage_ref")
    element_id = element.get("id")
    if not ref:
        return None

    from ..providers.llm.vision import complete_with_vision
    from .local_storage import asset_mime_type, public_url

    image_url = await public_url(ref)
    mime_type = await asset_mime_type(ref) if image_url else None
    if image_url is None or mime_type is None:
        return None

    try:
        result = await complete_with_vision(
            image_url=image_url,
            mime_type=mime_type,
            system="You are a meticulous visual analyzer for a marketing team.",
            question=(
                "Describe this image in detail, focusing on the main visual subjects, objects, "
                "colors, setting, and composition — grounded only in what you actually see."
            ),
        )
    except Exception as exc:
        log.warning("element_verified_description_failed", extra={"_extra_storage_ref": ref, "_extra_error": str(exc)})
        return None

    description = result.text.strip()
    if not description:
        return None

    if element_id:
        try:
            from ..models.base import async_session_factory
            from ..repositories.postgres.postgres_canvas_repository import PostgresCanvasRepository

            async with async_session_factory() as db:
                repo = PostgresCanvasRepository(db)
                entity = await repo.get_element(element_id)
                if entity is not None:
                    entity.metadata_json = {**(entity.metadata_json or {}), "verified_description": description}
                    await repo.update_element(entity)
        except Exception as exc:  # best-effort persistence — the fresh description is still
            # usable for THIS turn even if writing it back for future turns failed.
            log.warning("element_verified_description_persist_failed", extra={"_extra_element_id": element_id, "_extra_error": str(exc)})

    return description


async def build_element_context_blocks(
    element: dict[str, Any], *, thumbnail: bool = True
) -> list[dict[str, Any]]:
    """Builds ONE element's context as a content-block list: a text block with its real JSON
    metadata, followed by a real image block WHEN one exists for it. `element` is one entry of
    `referenced_elements_context` (or an equivalent dict: `storage_ref`, `element_type`,
    `description`).

    Rule for every caller: never reason about an element from this text block alone when it has a
    real image — always include the returned image block too. For a non-image element type
    (video/audio/text), no image block is attached (none exists to attach) — the text says so
    honestly rather than pretending text-only is equivalent.
    """
    ref = element.get("storage_ref")
    kind = element.get("element_type", "unknown")
    desc = element.get("description") or NO_DESCRIPTION_SENTINEL
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": f"Element (storage_ref: {ref}, type: {kind}) depicts:\n{desc}"}
    ]
    if kind == "image" and ref:
        loaded = await load_asset(ref)
        if loaded is not None:
            image_bytes, mime_type = loaded
            if thumbnail:
                image_bytes, mime_type = _downscale_thumbnail(image_bytes, mime_type)
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            blocks.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_img}"}}
            )
    return blocks
