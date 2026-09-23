"""Shared LLM + agent-loop configuration from services/config.ini [LLM]."""
from __future__ import annotations

from .. import config_loader

LLM_PROVIDER = (
    config_loader.get("LLM", "PROVIDER", "anthropic") or "anthropic"
).strip().lower()

LLM_API_KEY = (config_loader.get("LLM", "API_KEY", "") or "").strip()

LLM_MODEL = (
    config_loader.get("LLM", "MODEL", "claude-haiku-4-5-20251001")
    or "claude-haiku-4-5-20251001"
).strip()

LLM_BASE_URL = (config_loader.get("LLM", "BASE_URL", "") or "").strip()

# Required for identity-linked / multi-workspace Anthropic API keys.
# Claude Console → Settings → Workspaces → ID column (wrkspc_...).
LLM_WORKSPACE_ID = (
    config_loader.get("LLM", "WORKSPACE_ID", "")
    or config_loader.get("LLM", "ANTHROPIC_WORKSPACE_ID", "")
    or ""
).strip()

AGENT_MAX_TURNS = int(config_loader.get("LLM", "AGENT_MAX_TURNS", "12") or "12")


def llm_configured() -> bool:
    return bool(LLM_API_KEY)


def llm_health_status() -> str:
    if not LLM_API_KEY:
        return "missing"
    ws = f"+workspace" if LLM_WORKSPACE_ID else ""
    return f"{LLM_PROVIDER}:{LLM_MODEL}{ws}"
