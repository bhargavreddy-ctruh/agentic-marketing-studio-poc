"""
config_loader.py -- Central configuration loader for config.ini.

Parses backend/services/config.ini at import time and provides typed accessors
with fallback to environment variables for backward compatibility with .env.
"""

import configparser
import os
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent / "config.ini"
_parser = configparser.ConfigParser()
_parser.read(_CONFIG_PATH)


def has_option(section: str, key: str) -> bool:
    """Return True if config.ini has a non-empty value for section/key (ignores env)."""
    try:
        if not (_parser.has_section(section) and _parser.has_option(section, key)):
            return False
        return bool(_parser.get(section, key, fallback="").strip())
    except (configparser.NoSectionError, configparser.NoOptionError, KeyError):
        return False


def get(section: str, key: str, fallback: str = "") -> str:
    """
    Get a config value from the given section.

    Tries config.ini first, then environment variables:
    - {SECTION}_{KEY} (e.g. BRAND_GEMINI_KEY_1)
    - {KEY} (e.g. GEMINI_KEY_1)
    - fallback
    """
    try:
        if _parser.has_section(section) and _parser.has_option(section, key):
            val = _parser.get(section, key, fallback="").strip()
            if val:
                return val
    except (configparser.NoSectionError, configparser.NoOptionError, KeyError):
        pass

    env_val = os.getenv(f"{section}_{key}") or os.getenv(key) or fallback
    return (env_val or "").strip()


def get_list(section: str, key: str, fallback: str = "") -> list[str]:
    """
    Get a comma-separated config value as a list of trimmed strings.

    Example: MODEL=gemini-2.5-flash-lite,gemini-2.0-flash -> ["gemini-2.5-flash-lite", "gemini-2.0-flash"]
    """
    raw = get(section, key, fallback)
    return [v.strip() for v in raw.split(",") if v.strip()]


def get_keys(section: str, prefix: str = "GEMINI_KEY") -> list[str]:
    """
    Get API keys from config as a list (GEMINI_KEY_1, GEMINI_KEY_2, ...).

    Returns only non-empty keys. Supports up to 9 keys per section.
    Falls back to env vars {SECTION}_{PREFIX}_{i} or {PREFIX}_{i}.
    """
    keys: list[str] = []
    for i in range(1, 10):
        cfg_key = f"{prefix}_{i}"
        val = get(section, cfg_key, "")
        if val:
            keys.append(val)
    return keys
