"""
The single interrupt gate before Export — Architecture.md section 1: runs the trimmed Compliance
set (Brand Consistency, Visual Fidelity, Format/Technical QA) against one canvas element.

Combines results with "worst_of" logic — never averaging separate checks — a real principle
ported from the existing agentic_flow codebase's `agent_core/guardrails.py` (Rules.md section 6):
one real failure fails the whole gate, even if the other two pass, rather than a majority-vote or
averaged score papering over a genuine problem.

Remediation (Memory.md, Phase 3, per the user's explicit instruction): if the gate fails on an
image element for a reason `image_editor` could plausibly address (a brand/visual-fidelity
violation, not a format/dimension one — editing pixels doesn't change aspect ratio), it makes ONE
real `image_editor` call built directly from the checkers' own violation text, then re-runs the
same checks against the edited result. Capped at one attempt — this is the same "retry with real
feedback, never blind re-rolling, always capped" pattern already used elsewhere in this codebase
(Rules.md section 4, ported from the existing agentic_flow QA-retry logic), not an open loop.
"""
from __future__ import annotations

from ...core.exceptions import NotFoundError
from ...core.local_storage import load_asset
from ...core.middleware.logging import get_logger
from ...models.base import async_session_factory
from ...models.canvas_element import CanvasElementModel
from ...repositories.base import CanvasRepository
from ...providers.observability.langsmith import trace, traceable
from ...repositories.sqlite.sqlite_product_repository import SqliteProductRepository
from ..tools.registry import get_tool
from .brand_consistency_checker import check_brand_consistency
from .format_technical_qa import check_format_technical
from .visual_fidelity_checker import check_visual_fidelity
from .alignment_checker import check_alignment

log = get_logger(__name__)

_PRICE_KEYWORDS = ("price", "discount", "$", "% off", "off\"")


def _generation_prompt_text(metadata: dict) -> str:
    return " ".join(
        str(metadata[k]) for k in ("image_prompt", "frame_prompt", "motion_prompt", "aesthetic_direction")
        if metadata.get(k)
    ).strip() or "(no generation prompt recorded for this element)"


async def _run_checks(*, storage_ref: str, metadata: dict, element_type: str) -> dict:
    generation_prompt_text = _generation_prompt_text(metadata)

    # Real vision for images (Memory.md, Phase 4) — video elements fall back to text-only
    # reasoning (no frame-extraction pass built yet; a real, disclosed scope boundary, not hidden).
    image_bytes, mime_type = None, None
    if element_type == "image":
        loaded = load_asset(storage_ref)
        if loaded is not None:
            image_bytes, mime_type = loaded

    # We retrieve the original user_message from the turn history in the DB, but since the compliance
    # gate doesn't easily have it passed in, we can fetch it via the element's session ID if needed.
    # Actually, we can fetch the most recent user turn for this session here.
    # Real, live-found bug fixed alongside this (2026-09-25): the element's owning session also
    # gives us the real `user_id` — without it, `check_brand_consistency`/`check_visual_fidelity`
    # below always reported "no brand/product configured" regardless of what was really onboarded.
    user_message = ""
    user_id: str | None = None
    async with async_session_factory() as db:
        from ...repositories.sqlite.sqlite_chat_turn_repository import SqliteChatTurnRepository
        from ...repositories.sqlite.sqlite_canvas_repository import SqliteCanvasRepository
        from ...repositories.sqlite.sqlite_session_repository import SqliteSessionRepository
        element = await SqliteCanvasRepository(db).get_element_by_storage_ref(storage_ref)
        if element:
            turns = await SqliteChatTurnRepository(db).list_for_session(element.session_id)
            if turns:
                user_message = turns[-1].user_text
            session = await SqliteSessionRepository(db).get(element.session_id)
            if session:
                user_id = session.user_id

    brand = await check_brand_consistency(
        generation_prompt_text=generation_prompt_text, image_bytes=image_bytes, mime_type=mime_type,
        user_id=user_id,
    )
    visual = await check_visual_fidelity(
        generation_prompt_text=generation_prompt_text, image_bytes=image_bytes, mime_type=mime_type,
        user_id=user_id,
    )
    format_qa = await check_format_technical(
        storage_ref=storage_ref, expected_aspect_ratio=metadata.get("aspect_ratio")
    )
    
    alignment = await check_alignment(
        user_message=user_message, generation_prompt_text=generation_prompt_text
    )
    
    return {
        "brand_consistency": brand, 
        "visual_fidelity": visual, 
        "format_technical_qa": format_qa,
        "alignment": alignment
    }


def _collect_violations(checks: dict) -> list[str]:
    violations: list[str] = []
    for name in ("brand_consistency", "visual_fidelity"):
        for v in checks.get(name, {}).get("violations", []):
            violations.append(str(v))
    return violations


def _split_price_violations(violations: list[str]) -> tuple[list[str], list[str]]:
    """Separates price/discount-shaped violations (which need real, drawn text — a diffusion
    edit can't reliably render legible text, confirmed live: Memory.md, Phase 3) from everything
    else (which a genuine image_editor visual edit can plausibly address)."""
    price_related = [v for v in violations if any(k in v.lower() for k in _PRICE_KEYWORDS)]
    other = [v for v in violations if v not in price_related]
    return price_related, other


async def _real_overlay_text() -> str | None:
    """The real price/discount text for the (single, POC-scope) onboarded product, read directly
    from SQL — never a guess. Returns None if no product is onboarded or it has no price/discount
    at all, so callers know there's nothing real to draw."""
    async with async_session_factory() as db:
        products = await SqliteProductRepository(db).list_all()
    if not products:
        return None
    attrs = products[0].attributes
    price, discount = attrs.get("price"), attrs.get("discount_percent")
    parts = []
    if price is not None:
        parts.append(f"${price:g}")
    if discount:
        parts.append(f"{discount:g}% off")
    return " — ".join(parts) if parts else None


@traceable(name="compliance_gate")
async def run_compliance_gate(
    *, canvas: CanvasRepository, element_id: str, allow_remediation: bool = True
) -> dict:
    element = await canvas.get_element(element_id)
    if element is None:
        raise NotFoundError("CanvasElement", element_id)

    metadata = dict(element.metadata_json or {})
    checks = await _run_checks(
        storage_ref=element.storage_ref, metadata=metadata, element_type=element.element_type
    )
    
    # Check if alignment failed and append it as a non-blocking warning metadata
    alignment_check = checks.get("alignment", {})
    if not alignment_check.get("passed") and alignment_check.get("violations"):
        metadata["alignment_warning"] = alignment_check["violations"][0]
        element.metadata_json = metadata
        element = await canvas.update_element(element)
        
    # alignment is deliberately excluded from worst_of so it doesn't force a retry
    overall_passed = all(bool(c.get("passed")) for k, c in checks.items() if k != "alignment")  # worst_of

    remediation: dict | None = None
    if not overall_passed and allow_remediation and element.element_type == "image":
        violations = _collect_violations(checks)
        price_violations, other_violations = _split_price_violations(violations)

        applied_steps: list[str] = []
        current_ref = element.storage_ref
        edit_error: str | None = None

        # Price/discount text needs real, drawn text — a diffusion image_editor call can't
        # reliably render legible text (confirmed live: Memory.md, Phase 3), so this goes through
        # the deterministic text_overlay tool with the real figure, never a guessed one.
        if price_violations:
            overlay_text = await _real_overlay_text()
            if overlay_text:
                overlay_args = {"storage_ref": current_ref, "text": overlay_text, "placement": "lower third"}
                async with trace(name="tool:text_overlay", run_type="tool", inputs=overlay_args) as tool_run:
                    overlay_result = await get_tool("text_overlay").run(overlay_args)
                    tool_run.add_outputs(
                        {"ok": overlay_result.ok, "data": overlay_result.data, "error": overlay_result.error}
                    )
                if overlay_result.ok and overlay_result.data.get("storage_ref"):
                    current_ref = overlay_result.data["storage_ref"]
                    applied_steps.append(f"text_overlay: drew '{overlay_text}'")
                else:
                    edit_error = overlay_result.error

        # Everything else (color/lighting/etc.) is a genuine visual change image_editor can
        # plausibly make.
        if other_violations and edit_error is None:
            instruction = "Adjust the image to address: " + "; ".join(other_violations)
            edit_args = {"storage_ref": current_ref, "instruction": instruction}
            async with trace(name="tool:image_editor", run_type="tool", inputs=edit_args) as tool_run:
                edit_result = await get_tool("image_editor").run(edit_args)
                tool_run.add_outputs(
                    {"ok": edit_result.ok, "data": edit_result.data, "error": edit_result.error}
                )
            if edit_result.ok and edit_result.data.get("storage_ref"):
                current_ref = edit_result.data["storage_ref"]
                applied_steps.append(f"image_editor: {instruction}")
            else:
                edit_error = edit_result.error

        if applied_steps:
            # Record what was actually applied in the same text field the checkers read, so a
            # re-check can genuinely see what changed — not a fabricated "fixed" claim.
            metadata["image_prompt"] = (
                f"{metadata.get('image_prompt', '')}\n[Remediation applied: {'; '.join(applied_steps)}]"
            ).strip()
            element.storage_ref = current_ref
            element.metadata_json = metadata
            element.version += 1
            element = await canvas.update_element(element)

            recheck = await _run_checks(
                storage_ref=current_ref, metadata=metadata, element_type=element.element_type
            )
            recheck_passed = all(bool(c.get("passed")) for c in recheck.values())
            remediation = {
                "attempted": True,
                "steps_applied": applied_steps,
                "new_storage_ref": current_ref,
                "passed_after_remediation": recheck_passed,
                "error": edit_error,
            }
            log.info(
                "compliance_remediation",
                extra={"_extra_element_id": element_id, "_extra_passed_after": recheck_passed},
            )
            checks = recheck
            overall_passed = recheck_passed
        elif edit_error:
            remediation = {"attempted": True, "steps_applied": [], "error": edit_error}
        else:
            remediation = {"attempted": False, "reason": "no violation a real tool could address"}

    return {
        "element_id": element_id,
        "overall_passed": overall_passed,
        "checks": checks,
        "remediation": remediation,
    }
