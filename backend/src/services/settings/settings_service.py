"""
Runtime settings overrides (2026-09-28) — lets the user hand-edit `app_settings` rows directly in
Supabase's dashboard (no admin API/UI exists here on purpose — explicit user decision) and have
them take effect in the running app without a restart. See the plan's own Context section for why
this needs both a live `settings` mutation AND a provider-singleton reset: every provider bakes its
API key into its constructor via a lazy, process-lifetime singleton, so just mutating `settings`
alone wouldn't reach an already-built provider instance.
"""
from __future__ import annotations

from collections.abc import Callable

from ...core.config import settings
from ...core.middleware.logging import get_logger
from ...providers.image.cloudflare_flux import reset_cloudflare_flux_provider
from ...providers.image.huggingface import reset_image_edit_provider
from ...providers.llm.router import reset_llm_provider
from ...providers.observability.langsmith import configure_langsmith
from ...providers.video.replicate import reset_video_provider
from ...repositories.base import AppSettingRepository

log = get_logger(__name__)

# Feature toggles — checked directly off `settings` per-call wherever they're used, nothing bakes
# them into a constructor, so a live `setattr` alone is enough (confirmed via exploration; no
# resetter needed).
_BOOL_KEYS = {
    "compliance_qa_enabled",
    "multi_generation_parallel_enabled",
    "stream_llm_thinking_enabled",
    "canvas_versioning_enabled",
}

# Plain string Settings fields that ARE baked into a provider constructor.
_STR_SETTINGS_KEYS = {
    "groq_api_key",
    "huggingface_api_token",
    "cloudflare_account_id",
    "cloudflare_api_token",
    "falai_api_key",
    "replicate_api_token",
    "langsmith_api_key",
    # Product/Brand crawler (2026-09-28) — providers/crawlers/firecrawl_provider.py constructs its
    # client fresh on every call (crawls are infrequent, unlike the constantly-called LLM/image
    # providers), reading `settings.firecrawl_api_key` directly each time — so a live override
    # takes effect on the very next call with no cached singleton to reset, hence no resetter entry
    # below.
    "firecrawl_api_key",
}

# Not a `Settings` field at all — `core/local_storage.py` reads this straight from os.environ
# fresh on every call already, so it needs no resetter either.
_ENV_ONLY_KEYS = {"cloudinary_url": "CLOUDINARY_URL"}

# 2026-09-28: `openrouter_api_key`/`model_tier_1/2/3` removed from the whitelist entirely —
# confirmed dead code (OpenRouterProvider is never constructed anywhere `router.py` actually
# wires up; replicate.py is the only reachable video provider and Groq→Replicate the only
# reachable LLM chain). Kept in the DB briefly for consistency with a prior investigation, but
# there's no reason to keep listing keys that affect nothing live — see the plan's own note on
# deleting the now-stale rows from `app_settings` directly in Supabase.
WHITELIST: set[str] = _STR_SETTINGS_KEYS | _BOOL_KEYS | set(_ENV_ONLY_KEYS)

_KEY_TO_RESETTERS: dict[str, list[Callable[[], None]]] = {
    "groq_api_key": [reset_llm_provider],
    "replicate_api_token": [reset_llm_provider, reset_video_provider],
    "huggingface_api_token": [reset_image_edit_provider],
    "cloudflare_account_id": [reset_cloudflare_flux_provider],
    "cloudflare_api_token": [reset_cloudflare_flux_provider],
    "langsmith_api_key": [configure_langsmith],
}

_last_applied: dict[str, str] = {}


def apply_override(key: str, value: str) -> None:
    """Applies one whitelisted key's value live, then resets whichever provider singleton(s) that
    key maps to so the very next call rebuilds off the new value."""
    if key not in WHITELIST:
        log.warning("settings_override_skipped_not_whitelisted", extra={"_extra_key": key})
        return

    if key in _ENV_ONLY_KEYS:
        import os

        os.environ[_ENV_ONLY_KEYS[key]] = value
    elif key in _BOOL_KEYS:
        setattr(settings, key, value.strip().lower() in ("1", "true", "yes", "on"))
    else:
        setattr(settings, key, value)

    for resetter in _KEY_TO_RESETTERS.get(key, []):
        resetter()

    log.info("settings_override_applied", extra={"_extra_key": key})


async def sync_from_db(repo: AppSettingRepository) -> None:
    """Reads every row in `app_settings`, applies only the ones whose value actually changed since
    the last sync (an unrelated/unchanged row must never needlessly reset a provider singleton
    mid-request). Called once at startup (before any provider singleton can build itself off the
    un-overridden `.env` value) and then repeatedly by the background poll loop."""
    rows = await repo.list_all()
    for row in rows:
        if row.key not in WHITELIST:
            continue
        if _last_applied.get(row.key) == row.value:
            continue
        apply_override(row.key, row.value)
        _last_applied[row.key] = row.value
