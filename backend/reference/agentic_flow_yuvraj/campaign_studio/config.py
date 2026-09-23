"""
config.py — Configuration for Campaign Studio multi-agent service.

Agent reasoning uses [LLM] from agent_core / services/config.ini.
Image generation still uses the creative-studio provider chain separately.
"""
from __future__ import annotations

from .. import config_loader
from ..agent_core.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_PROVIDER,
    llm_configured,
    llm_health_status,
)

# Campaign-specific knobs
DEFAULT_ASSET_COUNT = int(config_loader.get("CAMPAIGN", "DEFAULT_ASSET_COUNT", "3") or "3")
MAX_ASSET_COUNT = int(config_loader.get("CAMPAIGN", "MAX_ASSET_COUNT", "6") or "6")
MAX_QA_RETRIES = int(config_loader.get("CAMPAIGN", "MAX_QA_RETRIES", "2") or "2")
DEFAULT_ASPECT_RATIO = config_loader.get("CAMPAIGN", "ASPECT_RATIO", "1:1") or "1:1"

# There is deliberately no AGENT_MAX_TURNS here. There used to be, read from
# [CAMPAIGN], exported, and read by nothing: every agent in crew.py passes its
# own max_turns because a Guardian returning one verdict and a Creative Director
# running a whole campaign do not want the same budget. A knob that silently
# does nothing is worse than no knob, so the setting is gone rather than wired
# to a number that would flatten those differences.

# Payload budget.
#
# The step endpoints are stateless, so every call resends every image the caller
# wants in scope: product photos, a logo, and any already-approved assets it is
# carrying forward. That is the design working as intended, and it is also how a
# request quietly grows until the worker dies on memory or the load balancer cuts
# it off with nothing in the log a caller can act on.
#
# Two limits rather than one. The per-image cap matches the 10 MB creative_studio
# already enforces, so a single bad upload fails the same way everywhere. The
# total is what actually protects the worker, because eight legal images still
# add up.
MAX_IMAGE_BYTES = int(
    config_loader.get("CAMPAIGN", "MAX_IMAGE_BYTES", str(10 * 1024 * 1024)) or 10 * 1024 * 1024
)
MAX_REQUEST_IMAGE_BYTES = int(
    config_loader.get("CAMPAIGN", "MAX_REQUEST_IMAGE_BYTES", str(32 * 1024 * 1024))
    or 32 * 1024 * 1024
)
MAX_PRIOR_ASSETS = int(config_loader.get("CAMPAIGN", "MAX_PRIOR_ASSETS", "12") or "12")

__all__ = [
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "DEFAULT_ASSET_COUNT",
    "MAX_ASSET_COUNT",
    "MAX_QA_RETRIES",
    "DEFAULT_ASPECT_RATIO",
    "MAX_IMAGE_BYTES",
    "MAX_REQUEST_IMAGE_BYTES",
    "MAX_PRIOR_ASSETS",
    "llm_configured",
    "llm_health_status",
]
