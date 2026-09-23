"""
Password hashing + signed session-cookie tokens — stdlib only, no new dependency (this project has
none of passlib/bcrypt/PyJWT/itsdangerous installed, and a POC-scope test auth flow doesn't need
one). `pbkdf2_hmac` is a real, standard, still-recommended KDF (not a toy) — "test-grade auth"
means one login mechanism with no email verification/reset/OAuth, not sloppy credential handling.

The session token is a signed, stateless cookie value (`user_id.expiry.hmac_signature`) — no
server-side session table needed, verified with a constant-time comparison
(`hmac.compare_digest`) so a timing attack can't be used to guess the signature byte-by-byte.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .config import settings

_PBKDF2_ITERATIONS = 600_000  # OWASP's current (2026) minimum recommendation for PBKDF2-SHA256


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Returns (hash_hex, salt_hex). Generates a real random salt when none is given (new user);
    pass the stored salt back in to verify an existing password against a fresh hash of it."""
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return derived.hex(), salt


def verify_password(password: str, *, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt=salt)
    # Constant-time comparison — a plain `==` leaks timing information about how many leading
    # bytes matched, letting an attacker guess the real hash byte-by-byte over many requests.
    return hmac.compare_digest(candidate, password_hash)


def create_session_token(user_id: str) -> str:
    expiry = int(time.time()) + settings.auth_session_ttl_seconds
    payload = f"{user_id}.{expiry}"
    signature = hmac.new(
        settings.auth_secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_session_token(token: str) -> str | None:
    """Returns the real user_id if the token is genuinely valid and not expired, else None —
    never raises, so a missing/malformed/tampered/expired cookie is always just "not logged in",
    never a 500."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id, expiry_str, signature = parts
    payload = f"{user_id}.{expiry_str}"
    expected_signature = hmac.new(
        settings.auth_secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    if expiry < int(time.time()):
        return None
    return user_id
