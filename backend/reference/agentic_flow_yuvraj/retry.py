"""
retry.py -- Generic retry utilities with exponential backoff, jitter,
and key rotation on 429/503 to avoid rate limits and service unavailability.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

from .gemini_key_rotator import APIKeyManager

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES = (429, 503)


def _backoff_seconds(attempt: int, base: float = 2.0, max_backoff: float = 60.0, jitter: bool = True) -> float:
    """Exponential backoff with optional jitter."""
    delay = min(base**attempt, max_backoff)
    if jitter:
        delay = delay * (0.5 + random.random())
    return delay


def _is_retryable(exc: BaseException) -> tuple[bool, int]:
    """
    Return (should_retry, status_code) for the exception.
    status_code is used for report_failure when applicable.
    """
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True, 429
    if status == 503:
        return True, 503
    # Check message for 503 (when status_code not passed)
    msg = str(exc).lower()
    if "503" in msg or "service unavailable" in msg:
        return True, 503
    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return True, 429
    # Timeout - retry as transient
    if "timeout" in msg or "timed out" in msg:
        return True, 504
    return False, 0


async def execute_with_retry(
    key_manager: APIKeyManager,
    operation: Callable[[str], Awaitable[T]],
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    max_backoff: float = 60.0,
    jitter: bool = True,
) -> T:
    """
    Execute an async operation with retry and key rotation on 429/503.

    The operation receives the API key and must use it for the request.
    On 429 or 503, the key is marked as failed (cooldown), and a new key
    is used for the next attempt.

    Args:
        key_manager: APIKeyManager for key rotation and failure reporting.
        operation: Async callable that takes (key: str) and returns result.
        max_attempts: Maximum number of attempts (default 3).
        backoff_base: Base for exponential backoff (default 2).
        max_backoff: Maximum backoff seconds (default 60).
        jitter: Add random jitter to backoff (default True).

    Returns:
        The result of the operation.

    Raises:
        The last exception if all attempts fail.
    """
    last_exc: BaseException | None = None
    key_used: str | None = None

    for attempt in range(max_attempts):
        key = key_manager.get_key()
        key_used = key

        try:
            result = await operation(key)
            key_manager.report_success(key)
            return result
        except Exception as exc:
            last_exc = exc
            retryable, status_code = _is_retryable(exc)
            if retryable and status_code in RETRYABLE_STATUS_CODES:
                key_manager.report_failure(key, status_code)
            elif retryable:
                # Timeout etc - don't mark key, just retry
                pass

            if attempt < max_attempts - 1 and retryable:
                delay = _backoff_seconds(attempt, backoff_base, max_backoff, jitter)
                logger.warning(
                    "[%s] Attempt %d failed (%s), retrying in %.1fs",
                    key_manager.service_name,
                    attempt + 1,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                raise

    if last_exc:
        raise last_exc
    raise RuntimeError("execute_with_retry: no result and no exception")


async def retry_with_model_fallback(
    key_manager: APIKeyManager,
    models: list[str],
    operation: Callable[[str, str], Awaitable[T]],
    max_retries_per_model: int = 2,
    backoff_base: float = 2.0,
    max_backoff: float = 60.0,
) -> T:
    """
    Try each model in order; for each model, retry with key rotation.

    The operation receives (model: str, key: str) and returns the result.
    If all models fail, raises the last exception.

    Args:
        key_manager: APIKeyManager for key rotation.
        models: Ordered list of model names to try.
        operation: Async callable (model, key) -> result.
        max_retries_per_model: Retries per model before trying next.
    """
    last_exc: BaseException | None = None

    for model in models:
        for attempt in range(max_retries_per_model):
            key = key_manager.get_key()
            try:
                result = await operation(model, key)
                key_manager.report_success(key)
                return result
            except Exception as exc:
                last_exc = exc
                retryable, status_code = _is_retryable(exc)
                if retryable and status_code in RETRYABLE_STATUS_CODES:
                    key_manager.report_failure(key, status_code)

                if attempt < max_retries_per_model - 1 and retryable:
                    delay = _backoff_seconds(attempt, backoff_base, max_backoff, True)
                    logger.warning(
                        "[%s] Model %s attempt %d failed, retrying in %.1fs",
                        key_manager.service_name,
                        model,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning("[%s] Model %s failed, trying next model", key_manager.service_name, model)
                    break

    if last_exc:
        raise last_exc
    raise RuntimeError("retry_with_model_fallback: no result and no exception")
