"""
chain.py -- Provider fallback orchestrator for unified image generation.

Tries each configured provider in order until one succeeds.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from .types import ImageGenRequest, ImageResult

if TYPE_CHECKING:
    from ..gemini_key_rotator import APIKeyManager

log = logging.getLogger(__name__)
# Use ctruh.main for provider/model logs so they appear in Docker/Gunicorn output
_main_log = logging.getLogger("ctruh.main")

PROVIDER_NAMES = ("gemini", "vertex", "aws")

# Prefixes of model names that are valid for each provider. Used to defensively
# filter any stray values (e.g. misconfigured env vars leaking a wrong-provider
# model name) out of the per-service override path before we hand them to a
# provider client that would only 404 on them.
_GEMINI_MODEL_PREFIXES = ("gemini-", "gemma-", "learnlm-")
_VERTEX_MODEL_PREFIXES = ("gemini-", "imagegeneration", "imagen-", "imagen3")
_AWS_MODEL_PREFIXES = ("amazon.", "anthropic.", "stability.", "meta.", "mistral.", "cohere.")


def _filter_models(models: list[str], prefixes: tuple[str, ...]) -> list[str]:
    """Keep only models whose name starts with one of the given prefixes."""
    return [m for m in models if any(m.startswith(p) for p in prefixes)]


def _resolve_gemini_image_models(section: str) -> list[str]:
    """Resolve the Gemini image-model list for a service section.

    Uses a provider-qualified per-service override key ``GEMINI_IMAGE_MODELS``
    to avoid env-var collisions with other sections (see the note in
    ``generate_with_chain``). Falls back to ``[GEMINI] IMAGE_MODELS`` and
    finally a hardcoded default.
    """
    from .. import config_loader

    override = config_loader.get_list(section, "GEMINI_IMAGE_MODELS", "")
    override = _filter_models(override, _GEMINI_MODEL_PREFIXES)
    if override:
        return override
    models = config_loader.get_list("GEMINI", "IMAGE_MODELS", "gemini-2.5-flash-image")
    models = _filter_models(models, _GEMINI_MODEL_PREFIXES) or models
    return models or ["gemini-2.5-flash-image"]


def _resolve_vertex_models(section: str) -> list[str]:
    """Resolve the Vertex image-model list for a service section."""
    from .. import config_loader

    override = config_loader.get_list(section, "VERTEX_MODEL", "")
    override = _filter_models(override, _VERTEX_MODEL_PREFIXES)
    if override:
        return override
    models = config_loader.get_list("VERTEX", "MODEL", "gemini-2.5-flash-image")
    models = _filter_models(models, _VERTEX_MODEL_PREFIXES) or models
    return models or ["gemini-2.5-flash-image"]


def _resolve_aws_models(section: str) -> list[str]:
    """Resolve the AWS Bedrock image-model list for a service section."""
    from .. import config_loader

    override = config_loader.get_list(section, "AWS_MODEL", "")
    override = _filter_models(override, _AWS_MODEL_PREFIXES)
    if override:
        return override
    models = config_loader.get_list("AWS", "MODEL", "amazon.titan-image-generator-v2:0")
    models = _filter_models(models, _AWS_MODEL_PREFIXES) or models
    return models or ["amazon.titan-image-generator-v2:0"]


async def generate_with_chain(
    request: ImageGenRequest,
    providers: list[str],
    key_manager: APIKeyManager,
    section: str,
    pacer: Callable[[str], Awaitable[None]] | None = None,
) -> ImageResult:
    """
    Generate image using the configured provider chain. Tries each provider
    in order until one succeeds.

    Args:
        request: Image generation request.
        providers: Ordered list of provider names ("gemini", "vertex", "aws").
        key_manager: APIKeyManager for Gemini key rotation.
        section: Config section (e.g. "CREATIVE") for loading provider-specific settings.
        pacer: Optional async callable awaited with the chosen API key before
            each Gemini HTTP call, used to smooth per-key request bursts.

    Returns:
        ImageResult with provider_used set to the successful provider.

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
            log.warning("Unknown provider %r, skipping", name)
            continue

        _main_log.debug("Image generation: trying provider=%s", name)
        try:
            if name == "gemini":
                # Model list must come from the provider's own section ([GEMINI]),
                # not the service section. Reading "MODEL" from the service section
                # (e.g. [TRYON]) is unsafe because config_loader.get falls back to
                # os.getenv("{SECTION}_{KEY}"), and the combined string can collide
                # with a bare env var intended for another section. For example,
                # an env var TRYON_MODEL=virtual-try-on-001 (legacy fallback for
                # [VERTEX].TRYON_MODEL) would be matched by get("TRYON", "MODEL"),
                # causing Gemini to receive the Vertex-only virtual-try-on-001
                # model and 404. Per-service overrides, if ever needed, must use a
                # provider-qualified key (e.g. GEMINI_IMAGE_MODELS) to avoid this.
                models = _resolve_gemini_image_models(section)
                from . import gemini_provider
                get_key_fn = getattr(key_manager, "acquire", None) or key_manager.get_key
                release_fn = getattr(key_manager, "release", None)
                n_keys = len(getattr(key_manager, "_active_keys", []) or [])
                result = await gemini_provider.generate_gemini(
                    request,
                    models=models,
                    get_key=get_key_fn,
                    report_success=key_manager.report_success,
                    report_failure=key_manager.report_failure,
                    pacer=pacer,
                    release_key=release_fn,
                    max_key_attempts=min(3, max(1, n_keys)),
                )
            elif name == "vertex":
                project = config_loader.get("VERTEX", "GOOGLE_CLOUD_PROJECT", "")
                region = config_loader.get("VERTEX", "GOOGLE_CLOUD_REGION", "us-central1")
                models = _resolve_vertex_models(section)
                if not project:
                    log.warning("Vertex provider: GOOGLE_CLOUD_PROJECT not configured, skipping")
                    continue
                from . import vertex_provider
                result = await vertex_provider.generate_vertex(
                    request, models=models, project=project, region=region
                )
            elif name == "aws":
                region = config_loader.get("AWS", "AWS_REGION", "us-east-1")
                api_key = config_loader.get("AWS", "API_KEY", "")
                access_key = config_loader.get("AWS", "AWS_ACCESS_KEY_ID", "")
                secret_key = config_loader.get("AWS", "AWS_SECRET_ACCESS_KEY", "")
                models = _resolve_aws_models(section)
                from . import bedrock_provider
                result = await bedrock_provider.generate_bedrock(
                    request,
                    models=models,
                    region=region,
                    api_key=api_key,
                    access_key=access_key,
                    secret_key=secret_key,
                )
            else:
                continue

            if result is not None:
                _main_log.debug(
                    "Image generated: provider=%s model=%s",
                    result.provider_used,
                    result.model_used or "unknown",
                )
                return result

        except Exception as exc:
            last_exc = exc
            log.warning(
                "Provider %s failed: %s, trying next in chain",
                name,
                exc,
                exc_info=False,
            )
            continue

    if last_exc:
        raise last_exc
    raise RuntimeError("All image providers failed (no image returned)")


def get_provider_status(section: str) -> dict:
    """
    Return configured providers and their availability for health checks.

    Args:
        section: Config section (e.g. "CREATIVE", "VERSA", "TRYON", "BRAND").

    Returns:
        Dict with provider_order and per-provider status ("configured" or "missing").
    """
    from .. import config_loader

    _primary = config_loader.get(section, "PROVIDER", "gemini").strip().lower()
    _fallbacks = config_loader.get_list(section, "FALLBACK", "")
    providers = [_primary] + [f.strip().lower() for f in _fallbacks if f.strip()]
    if not providers:
        providers = ["gemini", "vertex", "aws"]

    status: dict = {"provider_order": providers}

    # Gemini: keys in [GEMINI]
    keys = config_loader.get_keys("GEMINI", "GEMINI_KEY")
    legacy = config_loader.get("GEMINI", "GEMINI_API_KEY", "")
    status["gemini"] = "configured" if (keys or legacy) else "missing"

    # Vertex: project in [VERTEX]
    project = config_loader.get("VERTEX", "GOOGLE_CLOUD_PROJECT", "")
    status["vertex"] = "configured" if project else "missing"

    # AWS: API key or access keys in [AWS]
    api_key = config_loader.get("AWS", "API_KEY", "")
    access_key = config_loader.get("AWS", "AWS_ACCESS_KEY_ID", "")
    secret_key = config_loader.get("AWS", "AWS_SECRET_ACCESS_KEY", "")
    status["aws"] = "configured" if (api_key or (access_key and secret_key)) else "missing"

    return status
