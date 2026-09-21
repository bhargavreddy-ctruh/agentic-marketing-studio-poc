"""The public contract for the compliance gate's result."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ComplianceGateResponse(BaseModel):
    element_id: str
    overall_passed: bool
    checks: dict[str, Any]
    remediation: dict[str, Any] | None = None
