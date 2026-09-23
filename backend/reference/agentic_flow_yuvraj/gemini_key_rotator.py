"""
gemini_key_rotator.py -- Health-aware API key management with cooldown tracking.

Replaces blind round-robin with smart key selection: keys that hit 429/503
are marked as cooling down and skipped until the cooldown expires. Loads
keys from config.ini via config_loader, with env var fallback.

APIKeyManager is the primary class. GeminiKeyRotator is retained as an alias
for backward compatibility.
"""

import itertools
import logging
import threading
import time
from typing import Optional

from . import config_loader

logger = logging.getLogger(__name__)

# Cooldown parameters
_COOLDOWN_BASE_SEC = 15
_COOLDOWN_MAX_SEC = 120
_COOLDOWN_MULTIPLIER = 2


class APIKeyManager:
    """
    Health-aware API key manager with cooldown on rate-limit (429) and
    service-unavailable (503) errors.

    Args:
        service_name: Human-readable name for log messages.
        section: Config.ini section (e.g. "BRAND", "CREATIVE", "TRYON").
        legacy_env: Fallback env var when no keys in config (e.g. "GEMINI_API_KEY").
    """

    def __init__(
        self,
        service_name: str,
        section: str,
        legacy_env: str = "GEMINI_API_KEY",
    ):
        self.service_name = service_name
        self.section = section
        self._lock = threading.Lock()

        # Load keys from config.ini (with env fallback)
        self._active_keys: list[str] = []
        self._active_labels: list[str] = []
        for i, key in enumerate(config_loader.get_keys(section), start=1):
            if key:
                self._active_keys.append(key)
                self._active_labels.append(f"Key-{i}")

        # Per-key state: (cooldown_until_timestamp, consecutive_failures)
        self._key_state: dict[str, tuple[float, int]] = {k: (0.0, 0) for k in self._active_keys}

        # Legacy single-key fallback: prefer config.ini [section] (e.g. GEMINI_API_KEY),
        # then SECTION_KEY / KEY env vars via config_loader.
        self._legacy_key = config_loader.get(section, legacy_env, "").strip()

        # Round-robin over indices
        if self._active_keys:
            self._cycle = itertools.cycle(range(len(self._active_keys)))
        else:
            self._cycle = None

        logger.info(
            "[%s] APIKeyManager initialised: %d keys from [%s], legacy=%s",
            self.service_name,
            len(self._active_keys),
            section,
            "present" if self._legacy_key else "absent",
        )

    def get_key(self) -> str:
        """
        Return the next available API key, skipping keys in cooldown.

        If all keys are cooling down, returns the one whose cooldown expires soonest.
        Falls back to legacy key when no numbered keys are configured.
        """
        now = time.monotonic()

        if self._active_keys:
            with self._lock:
                # Find available keys (not in cooldown)
                available = [
                    i
                    for i in range(len(self._active_keys))
                    if self._key_state[self._active_keys[i]][0] <= now
                ]

                if available:
                    # Round-robin: advance cycle until we hit an available key
                    idx = next(self._cycle)
                    for _ in range(len(self._active_keys)):
                        if idx in available:
                            break
                        idx = next(self._cycle)
                else:
                    # All in cooldown — pick soonest to expire
                    idx = min(
                        range(len(self._active_keys)),
                        key=lambda i: self._key_state[self._active_keys[i]][0],
                    )
                    logger.warning(
                        "[%s] All keys cooling down, using %s (expires soonest)",
                        self.service_name,
                        self._active_labels[idx],
                    )

                key = self._active_keys[idx]
                label = self._active_labels[idx]
                logger.info("[%s] Using %s (health-aware rotation)", self.service_name, label)
                return key

        if self._legacy_key:
            logger.info("[%s] Using legacy GEMINI_API_KEY", self.service_name)
            return self._legacy_key

        raise RuntimeError(
            f"[{self.service_name}] No Gemini API keys configured. "
            f"Set keys in config.ini [{self.section}] or GEMINI_API_KEY"
        )

    def report_failure(self, key: str, status_code: int, retry_after_sec: Optional[float] = None) -> None:
        """
        Mark a key as failed (429 or 503). Puts it in cooldown.

        Cooldown duration: base 30s, exponential up to 5min per consecutive failure.
        """
        if status_code not in (429, 503):
            return

        with self._lock:
            if key not in self._key_state:
                return
            cooldown_until, failures = self._key_state[key]
            failures += 1
            if retry_after_sec is not None and retry_after_sec > 0:
                duration = min(float(retry_after_sec), _COOLDOWN_MAX_SEC)
            else:
                duration = min(
                    _COOLDOWN_BASE_SEC * (_COOLDOWN_MULTIPLIER ** (failures - 1)),
                    _COOLDOWN_MAX_SEC,
                )
            new_until = time.monotonic() + duration
            self._key_state[key] = (new_until, failures)
            label = self._get_label(key)
            logger.warning(
                "[%s] %s failed (HTTP %d), cooldown %.0fs (failure #%d)",
                self.service_name,
                label,
                status_code,
                duration,
                failures,
            )

    def report_success(self, key: str) -> None:
        """Reset failure count for a key on successful request."""
        with self._lock:
            if key in self._key_state:
                self._key_state[key] = (0.0, 0)

    def _get_label(self, key: str) -> str:
        try:
            idx = self._active_keys.index(key)
            return self._active_labels[idx]
        except ValueError:
            return "legacy"

    def has_keys(self) -> bool:
        """Return True if at least one usable key is available."""
        return bool(self._active_keys or self._legacy_key)

    def get_health_status(self) -> str:
        """Return a health-check string for the /health endpoint."""
        if self._active_keys:
            return f"loaded({len(self._active_keys)})"
        if self._legacy_key:
            return "loaded(legacy)"
        return "missing"

    def get_health_status_dict(self) -> dict:
        """Return detailed health status including per-key cooldown state."""
        now = time.monotonic()
        result: dict = {"keys_loaded": len(self._active_keys), "legacy": bool(self._legacy_key)}
        if self._active_keys:
            states = []
            for i, key in enumerate(self._active_keys):
                until, failures = self._key_state[key]
                available = until <= now
                states.append(
                    {
                        "label": self._active_labels[i],
                        "available": available,
                        "cooldown_sec": max(0, until - now) if not available else 0,
                        "consecutive_failures": failures,
                    }
                )
            result["key_states"] = states
        return result


# Backward compatibility alias
GeminiKeyRotator = APIKeyManager