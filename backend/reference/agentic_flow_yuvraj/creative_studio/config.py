"""
config.py -- Shared configuration for Ctruh AI Creative Studio service.

Configuration loaded from config.ini [CREATIVE] section with env var fallback.
"""

import asyncio
from pathlib import Path

from .. import config_loader
from ..gemini_key_rotator import APIKeyManager

# Ensure .env is loaded for any env-based overrides (e.g. in Docker)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _BACKEND_ROOT / ".env"
if _ENV_PATH.exists():
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH)

# -- Gemini API key manager from [GEMINI] ---------------------------------------
_key_manager = APIKeyManager(
    service_name="Creative-Studio",
    section="GEMINI",
    legacy_env="GEMINI_API_KEY",
)


def get_gemini_key() -> str:
    """Return the next Gemini API key via health-aware rotation."""
    return _key_manager.get_key()


def get_key_manager() -> APIKeyManager:
    """Return the API key manager for failure reporting."""
    return _key_manager


def gemini_health_status() -> str:
    """Return a health-check string for the Gemini key pool."""
    return _key_manager.get_health_status()


# Legacy alias — boolean for health checks
GEMINI_KEY: bool = _key_manager.has_keys()

# -- Image provider chain from [CREATIVE] PROVIDER + FALLBACK -------------------
_primary = config_loader.get("CREATIVE", "PROVIDER", "gemini").strip().lower()
_fallbacks = config_loader.get_list("CREATIVE", "FALLBACK", "")
IMAGE_PROVIDERS: list[str] = [_primary] + [f.strip().lower() for f in _fallbacks if f.strip()]
if not IMAGE_PROVIDERS:
    IMAGE_PROVIDERS = ["gemini", "vertex", "aws"]

# -- Video provider chain (VIDEO_PROVIDER + VIDEO_FALLBACK, else PROVIDER + FALLBACK) ---
_video_primary = config_loader.get("CREATIVE", "VIDEO_PROVIDER", "").strip().lower()
_video_fallbacks = config_loader.get_list("CREATIVE", "VIDEO_FALLBACK", "")
VIDEO_PROVIDERS: list[str] = (
    [_video_primary] + [f.strip().lower() for f in _video_fallbacks if f.strip()]
    if _video_primary
    else [_primary] + [f.strip().lower() for f in _fallbacks if f.strip()]
)
if not VIDEO_PROVIDERS:
    VIDEO_PROVIDERS = ["vertex", "gemini", "aws"]

# -- Gemini API constants ------------------------------------------------------
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

# Image model: dynamic from config.ini, fallback to hardcoded default
_image_models = config_loader.get_list("GEMINI", "IMAGE_MODELS", "gemini-3.1-flash-image-preview")
IMAGE_GENERATION_MODEL = _image_models[0] if _image_models else "gemini-3.1-flash-image-preview"

# Video model: hardcoded (no config override needed)
VIDEO_GENERATION_MODEL = "veo-3.1-generate-preview"

# -- Model fallback from [GEMINI] -----------------------------------------------
MODEL_LIST = config_loader.get_list("GEMINI", "MODEL", "gemini-2.5-flash-lite,gemini-2.0-flash")
FALLBACK_MODEL = config_loader.get("GEMINI", "FALLBACK", "gemini-2.0-flash")

# -- Video polling configuration -------------------------------------------------
VIDEO_POLL_INTERVAL_SECONDS = 8
VIDEO_POLL_MAX_ATTEMPTS = 375

# -- Concurrency limits (cap concurrent API calls to avoid rate-limit exhaustion) ---
_video_concurrency = int(config_loader.get("CREATIVE", "VIDEO_CONCURRENCY_LIMIT", "25"))
_image_concurrency = int(config_loader.get("CREATIVE", "IMAGE_CONCURRENCY_LIMIT", "55"))

VIDEO_SEMAPHORE = asyncio.Semaphore(_video_concurrency)
IMAGE_SEMAPHORE = asyncio.Semaphore(_image_concurrency)

# -- Image generation constraints ------------------------------------------------
SUPPORTED_ASPECT_RATIOS_IMAGE = [
    "1:1",   # Square
    "16:9",  # Landscape
    "9:16",  # Portrait
    "4:5",   # Instagram
    "2:3",   # Classic
    "3:2",   # Standard
    "4:3",   # iPad / Standard landscape
    "3:4",   # Portrait (supported by Gemini 3.1 Flash Image & Gemini 3 Pro Image)
    "5:4",   # Standard photo (supported by Gemini 3.1 Flash Image & Gemini 3 Pro Image)
    "1:2",   # Tall Banner
    "2:1",   # Panoramic
    "21:9",  # Ultrawide (supported by Gemini 3.1 Flash Image & Gemini 3 Pro Image)
]
SUPPORTED_ASPECT_RATIOS_VIDEO = ["16:9", "9:16"]
SUPPORTED_VIDEO_RESOLUTIONS = ["720p", "1080p", "4k"]
SUPPORTED_VIDEO_DURATIONS_SECONDS = [4, 6, 8]
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
SUPPORTED_IMAGE_MIME_TYPES = ["image/jpeg", "image/jpg", "image/png", "image/webp"]

# Normalize non-standard MIME type aliases before sending to Gemini.
_MIME_NORMALIZE: dict[str, str] = {
    "image/jpg":   "image/jpeg",
    "image/jfif":  "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}


def normalize_mime(mime: str) -> str:
    """Return the canonical MIME type Gemini accepts, resolving common aliases."""
    return _MIME_NORMALIZE.get(mime, mime)