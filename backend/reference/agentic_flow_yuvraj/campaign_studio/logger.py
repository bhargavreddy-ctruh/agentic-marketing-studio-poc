"""Logger for Campaign Studio (reuses creative_studio logger namespace)."""
from __future__ import annotations

from ..creative_studio.logger import get_logger, mask_key

__all__ = ["get_logger", "mask_key"]
