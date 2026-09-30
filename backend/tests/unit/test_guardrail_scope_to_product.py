"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found bug (2026-09-30): a session that has ever linked several distinct products
(confirmed live: three different phones in one real session) merged ALL of their identity rules
into one shared guardrails XML appended to every specialist's system prompt, with nothing marking
which product's "Name: X" rule applies to the current turn. Verifies `scope_to_product` keeps only
the resolved product's own rules (plus every non-product rule), and is a safe no-op when nothing
resolved this turn.
"""
from src.core.guardrails import GuardrailRule, GuardrailSet, scope_to_product


def _set() -> GuardrailSet:
    return GuardrailSet(rules=[
        GuardrailRule(id="brand.vis_1", rule="Only use brand colors X/Y/Z.", source="brand"),
        GuardrailRule(id="project.goal", rule="Work must serve this goal.", source="project"),
        GuardrailRule(id="product.p1.identity", rule="Name: Nothing Phone (2a)", source="product"),
        GuardrailRule(id="product.p1.must_show", rule="Show the camera module.", source="product"),
        GuardrailRule(id="product.p2.identity", rule="Name: Nothing Phone Neo 3", source="product"),
    ])


def test_scope_to_product_keeps_only_resolved_products_rules():
    scoped = scope_to_product(_set(), "p1")
    ids = {r.id for r in scoped.rules}
    assert ids == {"brand.vis_1", "project.goal", "product.p1.identity", "product.p1.must_show"}


def test_scope_to_product_none_is_a_no_op():
    original = _set()
    scoped = scope_to_product(original, None)
    assert {r.id for r in scoped.rules} == {r.id for r in original.rules}


def test_scope_to_product_unknown_id_drops_all_product_rules():
    scoped = scope_to_product(_set(), "does-not-exist")
    ids = {r.id for r in scoped.rules}
    assert ids == {"brand.vis_1", "project.goal"}
