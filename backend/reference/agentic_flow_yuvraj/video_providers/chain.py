"""
chain.py -- Video provider fallback orchestrator.

Tries each configured provider in order for submit; poll uses the provider
that successfully submitted.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .types import VideoGenRequest, VideoSubmitResult, VideoPollResult

if TYPE_CHECKING:
    from ..gemini_key_rotator import APIKeyManager

log = logging.getLogger(__name__)

PROVIDER_NAMES = ("gemini", "vertex", "aws")


async def submit_video_with_chain(
    request: VideoGenRequest,
    providers: list[str],
    key_manager: APIKeyManager,
    section: str,
) -> VideoSubmitResult:
    """
    Submit a video generation job using the configured provider chain.
    Tries each provider in order until one accepts the job.

    Args:
        request: Video generation request.
        providers: Ordered list of provider names ("gemini", "aws").
        key_manager: APIKeyManager for Gemini key rotation.
        section: Config section (e.g. "CREATIVE") for loading provider settings.

    Returns:
        VideoSubmitResult with operation_id, provider_used, model_used.

    Raises:
        The last exception if all providers fail.
    """
    from .. import config_loader

    providers = [p.strip().lower() for p in providers if p.strip()]
    if not providers:
        providers = ["gemini"]

    last_exc: Exception | None = None

    for name in providers:
        if name not in PROVIDER_NAMES:
            log.warning("Video chain: unknown provider %r, skipping", name)
            continue

        try:
            if name == "gemini":
                model = config_loader.get("GEMINI", "VIDEO_GENERATION_MODEL", "veo-3.1-generate-preview")
                if not model:
                    model = "veo-3.1-generate-preview"
                from . import gemini_video
                result = await gemini_video.submit_gemini_video(
                    request, model=model, key_manager=key_manager
                )
            elif name == "vertex":
                project = config_loader.get("VERTEX", "GOOGLE_CLOUD_PROJECT", "")
                region = config_loader.get("VERTEX", "GOOGLE_CLOUD_REGION", "us-central1")
                model = config_loader.get("VERTEX", "VIDEO_GENERATION_MODEL", "veo-3.1-generate-preview")
                if not project:
                    log.warning("Video chain: Vertex GOOGLE_CLOUD_PROJECT not set, skipping")
                    continue
                from . import vertex_video
                result = await vertex_video.submit_vertex_video(
                    request,
                    project=project,
                    region=region,
                    model=model or "veo-3.1-generate-preview",
                )
            elif name == "aws":
                region = config_loader.get("AWS", "AWS_REGION", "us-east-1")
                api_key = config_loader.get("AWS", "API_KEY", "")
                access_key = config_loader.get("AWS", "AWS_ACCESS_KEY_ID", "")
                secret_key = config_loader.get("AWS", "AWS_SECRET_ACCESS_KEY", "")
                s3_bucket = config_loader.get("AWS", "S3_BUCKET", "") or config_loader.get("AWS", "S3_OUTPUT_BUCKET", "")
                s3_prefix = config_loader.get("AWS", "S3_PREFIX", "").strip().rstrip("/")
                use_api_key_for_video = config_loader.get("AWS", "VIDEO_USE_API_KEY", "").lower() in ("1", "true", "yes")
                model = config_loader.get("AWS", "VIDEO_GENERATION_MODEL", "amazon.nova-reel-v1:1")
                if not model:
                    model = "amazon.nova-reel-v1:1"
                # Prefer API_KEY when VIDEO_USE_API_KEY=true (e.g. if IAM user lacks s3:PutObject)
                if use_api_key_for_video and api_key:
                    access_key, secret_key = "", ""
                from . import bedrock_video
                result = await bedrock_video.submit_bedrock_video(
                    request,
                    model=model,
                    region=region,
                    api_key=api_key,
                    access_key=access_key,
                    secret_key=secret_key,
                    s3_bucket=s3_bucket,
                    s3_prefix=s3_prefix,
                )
                if result is None:
                    continue
            else:
                continue

            return result

        except Exception as exc:
            last_exc = exc
            log.warning(
                "Video provider %s failed: %s, trying next in chain",
                name, exc, exc_info=False,
            )
            continue

    if last_exc:
        raise last_exc
    raise RuntimeError("All video providers failed")


async def poll_video_with_chain(
    operation_id: str,
    provider_used: str,
    key_manager: APIKeyManager,
    section: str,
    *,
    api_key_for_poll: str | None = None,
) -> VideoPollResult:
    """
    Poll a video operation using the provider that submitted it.

    Args:
        operation_id: Operation ID or invocation ARN from submit.
        provider_used: Provider that submitted (gemini, vertex, aws).
        key_manager: APIKeyManager for Gemini (used when provider is gemini/vertex).
        section: Config section (unused for poll, kept for API consistency).
        api_key_for_poll: For Gemini, the API key used at submit (required for poll).

    Returns:
        VideoPollResult with done, videos, data_url, mime_type, or error.
    """
    from .. import config_loader

    provider = provider_used.strip().lower()
    if provider not in PROVIDER_NAMES:
        raise ValueError(f"Unknown video provider for poll: {provider_used}")

    if provider == "gemini":
        from . import gemini_video
        return await gemini_video.poll_gemini_video(
            operation_id=operation_id,
            key_manager=key_manager,
            api_key_for_poll=api_key_for_poll,
        )

    if provider == "vertex":
        from . import vertex_video
        return await vertex_video.poll_vertex_video(operation_id)

    if provider == "aws":
        region = config_loader.get("AWS", "AWS_REGION", "us-east-1")
        api_key = config_loader.get("AWS", "API_KEY", "")
        use_api_key = config_loader.get("AWS", "VIDEO_USE_API_KEY", "").lower() in ("1", "true", "yes")
        access_key = "" if use_api_key and api_key else config_loader.get("AWS", "AWS_ACCESS_KEY_ID", "")
        secret_key = "" if use_api_key and api_key else config_loader.get("AWS", "AWS_SECRET_ACCESS_KEY", "")
        from . import bedrock_video
        return await bedrock_video.poll_bedrock_video(
            operation_id=operation_id,
            region=region,
            api_key=api_key,
            access_key=access_key,
            secret_key=secret_key,
        )

    raise ValueError(f"Unsupported video provider for poll: {provider_used}")
