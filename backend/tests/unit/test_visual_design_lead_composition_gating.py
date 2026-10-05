"""Regression test: composition_artist must not run as a blind second pass on a plain fresh
generation request — only when the request actually implies an edit/price/discount/creative-brief
ask (Memory.md fidelity audit, 2026-10-05)."""
from __future__ import annotations

from src.services.leads.visual_design_lead import _needs_composition_pass


def test_plain_generation_request_does_not_need_composition_pass() -> None:
    assert _needs_composition_pass("generate a photorealistic hero shot of the sneaker") is False
    assert _needs_composition_pass("make a youtube thumbnail for my unboxing video") is False


def test_price_and_edit_requests_do_need_composition_pass() -> None:
    assert _needs_composition_pass("add a 20% off discount badge") is True
    assert _needs_composition_pass("strike through the old price and show the new one") is True
    assert _needs_composition_pass("can you fix the logo in this image") is True
    assert _needs_composition_pass("give me a creative brief for this") is True
