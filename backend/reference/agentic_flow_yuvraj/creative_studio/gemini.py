"""
gemini.py -- All Gemini API interaction logic for Ctruh AI Creative Studio.

Provides two high-level async functions consumed by the API endpoints:

    generate_image(request: ImageGenerationRequest) -> ImageGenerationResult
        Uses the unified image provider chain (Gemini, Vertex, AWS).
        Accepts an optional base64 product image and a text prompt.
        Returns the generated image as a base64-encoded PNG/JPEG.

    submit_video_generation(request: VideoGenerationRequest) -> str
        Submits a Veo 3 video generation job and immediately returns the
        operation name (a long-running operation ID). Does NOT poll or wait.

    poll_video_operation(operation_name: str) -> VideoOperationResult
        Polls a single Veo operation by name. Returns a result object that
        tells the caller whether the operation is done and, if so, the video.

The separation of submit/poll is intentional: the endpoint layer owns the
polling loop and SSE streaming, keeping this module purely responsible for
Gemini API communication.

Error mapping:
    HTTP 429 from Gemini  -> GeminiRateLimitError  (surfaced as 429 to client)
    HTTP 5xx from Gemini  -> GeminiError            (surfaced as 502 to client)
    httpx timeout         -> GeminiTimeoutError     (surfaced as 504 to client)
    Any other exception   -> GeminiError            (surfaced as 502 to client)
"""
from __future__ import annotations

import asyncio
import base64
import httpx
from dataclasses import dataclass

from .config import (
    GEMINI_KEY,
    GEMINI_BASE_URL,
    IMAGE_GENERATION_MODEL,
    IMAGE_PROVIDERS,
    IMAGE_SEMAPHORE,
    VIDEO_PROVIDERS,
    VIDEO_SEMAPHORE,
    SUPPORTED_IMAGE_MIME_TYPES,
    MAX_IMAGE_SIZE_BYTES,
    normalize_mime,
    get_gemini_key,
    get_key_manager,
)
from .exceptions import (
    GeminiError,
    GeminiRateLimitError,
    GeminiTimeoutError,
    ImageTooLargeError,
    UnsupportedMediaError,
    InvalidInputError,
)
from ..image_providers import ImageGenRequest, ImageInput, generate_with_chain
from .logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageGenerationRequest:
    """
    All inputs needed to generate a product image via Gemini Flash.

    Fields:
        prompt          -- Text describing the desired transformation or scene.
        image_base64    -- Optional base64-encoded source image (product photo).
                          If omitted, the model generates purely from the prompt.
        image_mime_type -- MIME type of image_base64 ("image/jpeg", "image/png",
                          "image/webp"). Ignored when image_base64 is None.
        source_images   -- Optional list of reference images, e.g. a product's
                          front and back — [{"data": b64, "mime_type": "..."}].
                          Takes priority over image_base64 when given, so a
                          caller with several photos does not have to throw the
                          rest away to fit one field. Order matters: providers
                          that read it as a sequence (rather than an unordered
                          set of references) do so in this order.
        aspect_ratio    -- Desired output aspect ratio ("1:1", "4:3", "16:9",
                          "9:16", "3:4"). Defaults to "1:1".
        brand_memory    -- Optional free-text brand context injected into the
                          system instruction to guide on-brand outputs.
    """
    prompt:          str
    image_base64:    str | None = None
    image_mime_type: str        = "image/jpeg"
    source_images:   list[dict[str, str]] | None = None
    aspect_ratio:    str        = "1:1"
    brand_memory:    str | None = None


@dataclass
class ImageGenerationResult:
    """
    Output from a successful image generation call.

    Fields:
        image_base64   -- Base64-encoded generated image data (no data-URI prefix).
        mime_type      -- MIME type of the generated image (e.g. "image/png").
        data_url       -- Fully formed data URI: "data:<mime_type>;base64,<data>".
        provider_used  -- Provider that generated the image (gemini, vertex, aws).
        model_used     -- Model name used (e.g. gemini-2.5-flash-image).
    """
    image_base64:   str
    mime_type:      str
    data_url:       str
    provider_used:  str = ""
    model_used:     str | None = None


@dataclass
class VideoGenerationRequest:
    """
    All inputs needed to submit a Veo 3 video generation job.

    Fields:
        prompt            -- Text describing the desired video motion and scene.
        image_base64      -- Base64 start frame (before). Required for before/after.
        image_mime_type   -- MIME type of image_base64.
        end_image_base64  -- Base64 end frame (after). Required for before/after.
        end_image_mime_type -- MIME type of end_image_base64.
        aspect_ratio      -- Desired output aspect ratio ("16:9", "9:16", "1:1").
        sample_count      -- Number of video samples to generate (1 or 2).
        duration_seconds  -- Video duration in seconds (3, 5, or 10).
        resolution        -- Video resolution ("720p", "1080p", "4k").
        brand_memory      -- Optional brand context appended to the prompt.
    """
    prompt:               str
    image_base64:         str | None = None
    image_mime_type:      str        = "image/jpeg"
    end_image_base64:     str | None = None
    end_image_mime_type:  str        = "image/jpeg"
    aspect_ratio:         str        = "16:9"
    sample_count:         int        = 1
    duration_seconds:     int        = 5
    resolution:           str        = "1080p"
    brand_memory:         str | None = None


@dataclass
class VideoItem:
    """A single generated video result."""
    data_url:  str
    mime_type: str


@dataclass
class VideoOperationResult:
    """
    Result of a single poll against a Veo long-running operation.

    Fields:
        done      -- True when the operation has completed (success or error).
        videos    -- List of VideoItem results (one per sample_count). Present
                     only when done=True and operation succeeded.
        data_url  -- Convenience: data_url of the first video (videos[0]).
        mime_type -- MIME type of the first video.
        error     -- Error message string when done=True and operation failed.
    """
    done:      bool
    videos:    list = None          # list[VideoItem]
    data_url:  str | None = None    # videos[0].data_url shortcut
    mime_type: str | None = None    # videos[0].mime_type shortcut
    error:     str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_image(image_base64: str, mime_type: str) -> str:
    """
    Validate base64 image size and MIME type before sending to Gemini.
    Returns the normalized (canonical) MIME type.
    Raises ImageTooLargeError or UnsupportedMediaError on failure.
    """
    normalized = normalize_mime(mime_type)
    if normalized not in SUPPORTED_IMAGE_MIME_TYPES:
        raise UnsupportedMediaError(
            f"Unsupported image MIME type '{mime_type}'. "
            f"Accepted: {', '.join(SUPPORTED_IMAGE_MIME_TYPES)}"
        )
    try:
        decoded_size = len(base64.b64decode(image_base64, validate=True))
    except Exception:
        raise InvalidInputError("image_base64 is not valid base64-encoded data.")

    if decoded_size > MAX_IMAGE_SIZE_BYTES:
        mb = decoded_size / (1024 * 1024)
        raise ImageTooLargeError(
            f"Image is {mb:.1f} MB; maximum allowed size is "
            f"{MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB."
        )

    return normalized


def _gemini_url(path: str) -> str:
    """Construct a full Gemini API URL. Auth is passed via x-goog-api-key header."""
    return f"{GEMINI_BASE_URL}/{path}"


async def _post_json(url: str, payload: dict, timeout: float = 180.0) -> dict:
    """
    Execute a POST request to the Gemini API and return the parsed JSON body.

    Maps HTTP error codes to the appropriate CreativeStudioError subclass:
        429 -> GeminiRateLimitError
        5xx -> GeminiError
        4xx -> GeminiError (with the API error message included)
    Timeout raises GeminiTimeoutError.
    """
    log.debug("POST %s", url)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": get_gemini_key(),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise GeminiTimeoutError(f"Gemini API request timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise GeminiError(f"Gemini API network error: {exc}") from exc

    if resp.status_code == 429:
        body = resp.json() if resp.content else {}
        msg  = body.get("error", {}).get("message", "Gemini quota exceeded")
        raise GeminiRateLimitError(msg)

    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg  = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
        except Exception:
            msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
        raise GeminiError(f"Gemini API error: {msg}")

    try:
        return resp.json()
    except Exception as exc:
        raise GeminiError(f"Gemini API returned non-JSON response: {exc}") from exc


async def _get_json(url: str, timeout: float = 30.0) -> dict:
    """
    Execute a GET request to the Gemini API and return the parsed JSON body.
    Used for polling long-running operation status.
    """
    log.debug("GET %s", url)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": get_gemini_key(),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise GeminiTimeoutError(f"Gemini poll request timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise GeminiError(f"Gemini poll network error: {exc}") from exc

    if resp.status_code == 429:
        body = resp.json() if resp.content else {}
        msg  = body.get("error", {}).get("message", "Gemini quota exceeded")
        raise GeminiRateLimitError(msg)

    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg  = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
        except Exception:
            msg = f"HTTP {resp.status_code}"
        raise GeminiError(f"Gemini poll API error: {msg}")

    try:
        return resp.json()
    except Exception as exc:
        raise GeminiError(f"Gemini poll returned non-JSON: {exc}") from exc


def _build_system_instruction(brand_memory: str | None) -> str:
    """
    Build the system instruction string for Gemini.
    When brand_memory is provided, the instruction instructs the model to apply
    the brand's visual identity to all outputs.
    """
    base = (
        "You are a professional product photographer and commercial image editor. "
        "Produce studio-quality, photorealistic outputs ready for e-commerce and marketing use. "
        "Follow the transformation prompt exactly. Do not add watermarks or overlays."
    )
    if brand_memory and brand_memory.strip():
        return (
            f"{base}\n\n"
            "Brand identity context (apply consistently to all outputs):\n"
            f"{brand_memory.strip()}\n\n"
            "Ensure the color palette, visual style, and overall aesthetic of the output "
            "are consistent with the brand identity described above."
        )
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Image generation
# ─────────────────────────────────────────────────────────────────────────────

async def generate_image(request: ImageGenerationRequest) -> ImageGenerationResult:
    """
    Generate a product image using the configured provider chain (Gemini, Vertex, AWS).

    If request.image_base64 is provided, it is sent as a multimodal input alongside
    the text prompt, enabling image-to-image transformation. If omitted, the model
    generates an image from the prompt alone (text-to-image).

    The aspect_ratio is appended to the prompt as a generation hint.

    Raises:
        InvalidInputError      -- prompt is blank after stripping
        ImageTooLargeError     -- base64 image exceeds MAX_IMAGE_SIZE_BYTES
        UnsupportedMediaError  -- image MIME type is not in SUPPORTED_IMAGE_MIME_TYPES
        GeminiRateLimitError   -- Gemini returned HTTP 429
        GeminiTimeoutError     -- Gemini did not respond within the timeout
        GeminiError            -- Any other API failure (after all providers tried)
    """
    if not request.prompt or not request.prompt.strip():
        raise InvalidInputError("prompt must not be empty.")

    # A list of references takes priority over the single-image field, so a
    # caller with a product's front and back does not have to pick one and
    # discard the other. Each is validated the same way the single image
    # always was — same size limit, same MIME allowlist — just per image
    # rather than once.
    source_images = None
    if request.source_images:
        validated = []
        for img in request.source_images:
            data = img.get("data") or img.get("data_b64") or ""
            if not data:
                continue
            mime = _validate_image(data, img.get("mime_type") or "image/jpeg")
            validated.append(ImageInput(data_b64=data, mime_type=mime))
        source_images = validated or None
    elif request.image_base64:
        request.image_mime_type = _validate_image(request.image_base64, request.image_mime_type)
        source_images = [ImageInput(data_b64=request.image_base64, mime_type=request.image_mime_type)]

    user_text = (
        f"{request.prompt.strip()}\n\n"
        f"Output aspect ratio: {request.aspect_ratio}. "
        "Commercial product photography quality. Clean, polished, ready for e-commerce."
    )

    gen_request = ImageGenRequest(
        prompt=user_text,
        source_images=source_images,
        aspect_ratio=request.aspect_ratio,
        system_instruction=_build_system_instruction(request.brand_memory),
    )

    log.info(
        "generate_image: providers=%s aspect=%s source_images=%d",
        IMAGE_PROVIDERS, request.aspect_ratio, len(source_images or []),
    )

    try:
        async with IMAGE_SEMAPHORE:
            result = await generate_with_chain(
                request=gen_request,
                providers=IMAGE_PROVIDERS,
                key_manager=get_key_manager(),
                section="CREATIVE",
            )
    except Exception as exc:
        if isinstance(exc, (InvalidInputError, ImageTooLargeError, UnsupportedMediaError)):
            raise
        if "429" in str(exc) or "rate limit" in str(exc).lower() or "quota" in str(exc).lower():
            raise GeminiRateLimitError(str(exc))
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            raise GeminiTimeoutError(str(exc))
        raise GeminiError(str(exc))

    log.info(
        "generate_image: success provider=%s model=%s mime_type=%s",
        result.provider_used, result.model_used, result.mime_type,
    )
    return ImageGenerationResult(
        image_base64=result.image_base64,
        mime_type=result.mime_type,
        data_url=result.data_url,
        provider_used=result.provider_used,
        model_used=result.model_used,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Video generation -- submit
# ─────────────────────────────────────────────────────────────────────────────

async def submit_video_generation(request: VideoGenerationRequest) -> str:
    """
    Submit a Veo 3 video generation job and return the operation name.

    This function returns immediately after the job is accepted by the Gemini API.
    It does NOT wait for the video to be ready. Use poll_video_operation() with
    the returned operation name to check progress and retrieve the result.

    The operation name has the format:
        "operations/AIzaSy..."

    If brand_memory is provided, it is appended to the prompt so the model
    produces on-brand motion and scene composition.

    Raises:
        InvalidInputError     -- prompt is blank after stripping
        ImageTooLargeError    -- base64 start frame exceeds MAX_IMAGE_SIZE_BYTES
        UnsupportedMediaError -- start frame MIME type is not supported
        GeminiRateLimitError  -- Gemini returned HTTP 429
        GeminiTimeoutError    -- Gemini did not respond within the timeout
        GeminiError           -- Any other Gemini API failure or missing operation name
    """
    if not request.prompt or not request.prompt.strip():
        raise InvalidInputError("prompt must not be empty.")
    if not request.image_base64:
        raise InvalidInputError("image_base64 (start frame) is required for video generation.")

    if request.image_base64:
        request.image_mime_type = _validate_image(request.image_base64, request.image_mime_type)

    # Only validate end frame when a distinct end image is provided.
    # Do NOT copy start→end: identical lastFrame is rejected by Gemini Veo
    # ("Your use case is currently not supported") unless duration is 8s,
    # and single-frame image-to-video works better without lastFrame.
    if request.end_image_base64 and request.end_image_base64 != request.image_base64:
        request.end_image_mime_type = _validate_image(request.end_image_base64, request.end_image_mime_type)
    elif request.end_image_base64 and request.end_image_base64 == request.image_base64:
        request.end_image_base64 = None

    effective_prompt = request.prompt.strip()
    if request.brand_memory and request.brand_memory.strip():
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"Brand context: {request.brand_memory.strip()}\n"
            "Apply the brand identity to the visual style, lighting, and scene composition."
        )

    log.info(
        "submit_video: providers=%s aspect=%s sample_count=%d has_start_frame=%s has_end_frame=%s",
        VIDEO_PROVIDERS, request.aspect_ratio,
        request.sample_count, request.image_base64 is not None,
        request.end_image_base64 is not None,
    )

    from ..video_providers.chain import submit_video_with_chain
    from ..video_providers.types import VideoGenRequest

    async with VIDEO_SEMAPHORE:
        vreq = VideoGenRequest(
            prompt=effective_prompt,
            image_base64=request.image_base64,
            image_mime_type=request.image_mime_type,
            end_image_base64=request.end_image_base64,
            end_image_mime_type=request.end_image_mime_type,
            aspect_ratio=request.aspect_ratio,
            sample_count=request.sample_count,
            duration_seconds=request.duration_seconds,
            resolution=request.resolution,
            brand_memory=request.brand_memory,
        )
        result = await submit_video_with_chain(
            request=vreq,
            providers=VIDEO_PROVIDERS,
            key_manager=get_key_manager(),
            section="CREATIVE",
        )
    # Return "provider:operation_id" so poll knows which provider to use
    operation_name = f"{result.provider_used}:{result.operation_id}"
    if result.api_key_for_poll:
        async with _video_poll_keys_lock:
            _video_poll_keys[operation_name] = result.api_key_for_poll
    log.info("submit_video: operation accepted name=%s provider=%s", result.operation_id, result.provider_used)
    return operation_name


# ─────────────────────────────────────────────────────────────────────────────
# Video generation -- poll
# ─────────────────────────────────────────────────────────────────────────────

# Cache of operation_name -> api_key_for_poll (Gemini only; cleared after poll completes)
_video_poll_keys: dict[str, str] = {}
_video_poll_keys_lock = asyncio.Lock()


def _parse_operation_for_poll(operation_name: str) -> tuple[str, str]:
    """Parse operation_name to (provider, operation_id). Supports 'provider:op_id' or raw op_id."""
    if ":" in operation_name:
        provider, op_id = operation_name.split(":", 1)
        return provider.strip().lower(), op_id.strip()
    # Infer provider from operation_id format
    if operation_name.startswith("projects/"):
        return "vertex", operation_name
    if operation_name.startswith("operations/"):
        return "gemini", operation_name
    return "aws", operation_name


async def poll_video_operation(operation_name: str) -> VideoOperationResult:
    """
    Poll a single Veo long-running operation and return its current status.

    The caller is responsible for the polling loop and sleep intervals. This
    function performs exactly one GET request per call.

    operation_name may be "provider:operation_id" (from submit) or raw operation_id.

    Returns VideoOperationResult with done=False if the operation is still running.
    Returns VideoOperationResult with done=True and data_url set if the video is ready.
    Returns VideoOperationResult with done=True and error set if the operation failed.

    Raises:
        GeminiRateLimitError -- Gemini returned HTTP 429 during polling
        GeminiTimeoutError   -- Poll GET request timed out
        GeminiError          -- Any other Gemini API failure during polling
    """
    provider, operation_id = _parse_operation_for_poll(operation_name)
    async with _video_poll_keys_lock:
        api_key_for_poll = _video_poll_keys.get(operation_name)

    from ..video_providers.chain import poll_video_with_chain

    result = await poll_video_with_chain(
        operation_id=operation_id,
        provider_used=provider,
        key_manager=get_key_manager(),
        section="CREATIVE",
        api_key_for_poll=api_key_for_poll,
    )

    if result.done:
        async with _video_poll_keys_lock:
            _video_poll_keys.pop(operation_name, None)

    return VideoOperationResult(
        done=result.done,
        data_url=result.data_url,
        mime_type=result.mime_type,
        error=result.error,
        videos=[VideoItem(data_url=v.data_url, mime_type=v.mime_type) for v in (result.videos or [])],
    )
