"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05, explicit user ask: "every llm should know what parameters/images/
everything they have... without filling useless/bloating the llm with unnecessary data"):
`available_context_block` replaces the old `json.dumps(brief)` tails every Lead used to hand a
specialist. Verifies: it states what's genuinely available (product/references/deliverable/script)
without dumping internal scratch keys, and keeps long referenced-element descriptions bounded."""
from __future__ import annotations

from src.services.leads.base import available_context_block, referenced_element_block


def test_states_resolved_product_and_excludes_scratch_keys():
    brief = {
        "resolved_product_id": "prod-123",
        "_error": "should never appear",
        "_resume_direct_fix_context": "internal bookkeeping, should never appear",
        "_element_disambiguation_needed": True,
    }
    block = available_context_block(brief)
    assert "prod-123" in block
    assert "product_lookup" in block
    assert "_error" not in block
    assert "_resume_direct_fix_context" not in block
    assert "_element_disambiguation_needed" not in block


def test_no_product_states_that_plainly():
    block = available_context_block({})
    assert "No specific product is resolved" in block


def test_truncates_long_referenced_element_description():
    long_desc = "A very detailed essay. " * 50
    brief = {"referenced_elements_context": [
        {"id": "e1", "storage_ref": "ref1", "element_type": "image", "description": long_desc},
    ]}
    block = available_context_block(brief)
    assert "ref1" in block
    assert "…" in block
    assert len(long_desc) > 300
    assert long_desc.strip() not in block  # the full essay must not appear verbatim


def test_caps_referenced_element_preview_to_three_with_a_count_note():
    brief = {"referenced_elements_context": [
        {"id": f"e{i}", "storage_ref": f"ref{i}", "element_type": "image"} for i in range(5)
    ]}
    block = available_context_block(brief)
    assert "ref0" in block and "ref1" in block and "ref2" in block
    assert "ref3" not in block
    assert "2 more" in block


def test_approved_script_and_deliverable_surfaced():
    brief = {"approved_script": "Hey everyone...", "deliverable": "youtube_thumbnail"}
    block = available_context_block(brief)
    assert "already approved" in block
    assert "youtube_thumbnail" in block


def test_referenced_element_block_truncates_long_description():
    long_desc = "A very detailed essay. " * 50
    brief = {
        "latest_element_storage_ref": "ref1", "latest_element_type": "image",
        "latest_element_description": long_desc,
    }
    block = referenced_element_block(brief)
    assert "…" in block
    assert long_desc.strip() not in block
