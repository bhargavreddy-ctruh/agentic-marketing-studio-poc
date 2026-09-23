"""
tools.py — Tool schemas + handlers for Campaign Studio agents.

Image generation uses creative_studio provider chain.
Structured submit tools are terminal for their respective agents.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..agent_core.brand_context import (
    BrandDetails,
    ProjectDetails,
    coerce_brand,
    coerce_product,
    coerce_project,
    render_context,
    visual_memory,
)
from ..agent_core.gates import (
    GATE_DIRECTION,
    GATE_PRE_IMAGE,
    Approvals,
    GateSet,
    gate_payload,
)
from ..agent_core.guardian import (
    ALL_TARGETS,
    TARGET_IMAGE,
    GuardianVerdict,
    Violation,
)
from ..agent_core.escalations import EscalationDecisions
from ..agent_core.revision import coerce_scene_revisions, render_image_revision
from ..agent_core.guardrails import (
    SCOPE_CAMPAIGN,
    SOURCE_HUMAN,
    GuardrailRule,
    GuardrailSet,
    resolve as resolve_guardrails,
)
from ..creative_studio.gemini import ImageGenerationRequest, generate_image
from .agent_runtime import EmitFn, current_agent, terminal
from .config import DEFAULT_ASPECT_RATIO, MAX_QA_RETRIES
from .llm_provider import image_block
from .logger import get_logger

log = get_logger(__name__)


# ── Anthropic tool JSON schemas ──────────────────────────────────────────────

TOOL_VIEW_PRODUCT_IMAGES = {
    "name": "view_product_images",
    "description": "Look at the brand's uploaded product reference photos. Call this before designing scenes or QA.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

TOOL_VIEW_GENERATED_IMAGE = {
    "name": "view_generated_image",
    "description": "Look at a generated campaign image for a given scene_id.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene_id": {"type": "string", "description": "Scene id, e.g. scene_1"},
        },
        "required": ["scene_id"],
    },
}

TOOL_GENERATE_SCENE_IMAGE = {
    "name": "generate_scene_image",
    "description": (
        "Generate a campaign image for a storyboard scene using the product photos as reference. "
        "Pass a detailed prompt that preserves the exact product appearance."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scene_id": {"type": "string"},
            "prompt": {"type": "string", "description": "Detailed image generation prompt"},
            "aspect_ratio": {"type": "string", "description": "e.g. 1:1, 4:5, 9:16"},
            "title": {"type": "string"},
        },
        "required": ["scene_id", "prompt"],
    },
}

TOOL_SUBMIT_CONCEPT = {
    "name": "submit_concept",
    "description": "Submit the campaign concept once you have decided theme, tone, audience, and shot count.",
    "input_schema": {
        "type": "object",
        "properties": {
            "theme": {"type": "string"},
            "tone": {"type": "string"},
            "targetAudience": {"type": "string"},
            "visualStyle": {"type": "string"},
            "shotCount": {"type": "integer"},
            "summary": {"type": "string"},
        },
        "required": ["theme", "tone", "targetAudience", "visualStyle", "shotCount", "summary"],
    },
}

TOOL_SUBMIT_STORYBOARD = {
    "name": "submit_storyboard",
    "description": (
        "Submit the final storyboard scenes for the campaign, in order. Scene ids are "
        "assigned from that order — scene_1, scene_2, and so on — so do not supply one: "
        "other nodes were wired to those numbers before you ran, and a descriptive id "
        "names a scene nothing can find. Put the meaning in `title`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "composition": {"type": "string"},
                        "imagePrompt": {"type": "string"},
                    },
                    "required": ["title", "description", "imagePrompt"],
                },
            },
        },
        "required": ["scenes"],
    },
}

TOOL_SUBMIT_COPY = {
    "name": "submit_copy",
    "description": "Submit final marketing copy for the campaign.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "tagline": {"type": "string"},
            "cta": {"type": "string"},
            "instagramCaption": {"type": "string"},
            "adBannerText": {"type": "string"},
            "sceneCaptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sceneId": {"type": "string"},
                        "caption": {"type": "string"},
                    },
                },
            },
        },
        "required": ["headline", "tagline", "cta", "instagramCaption"],
    },
}

TOOL_SUBMIT_VERDICT = {
    "name": "submit_verdict",
    "description": "Submit the QA pass/fail verdict for a generated scene image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene_id": {"type": "string"},
            "passed": {"type": "boolean"},
            "score": {"type": "integer"},
            "productFidelity": {"type": "string", "enum": ["pass", "fail"]},
            "brandAlignment": {"type": "string", "enum": ["pass", "fail"]},
            "briefConsistency": {"type": "string", "enum": ["pass", "fail"]},
            "feedback": {"type": "string"},
        },
        "required": ["scene_id", "passed", "score", "feedback"],
    },
}

TOOL_SUBMIT_GUARDIAN_VERDICT = {
    "name": "submit_guardian_verdict",
    "description": (
        "Submit the brand-compliance verdict for one piece of work. This is a separate "
        "judgement from QA: you judge only whether the work matches the brand."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": list(ALL_TARGETS)},
            "subject_id": {
                "type": "string",
                "description": "Scene id for image and video targets; omit otherwise.",
            },
            "passed": {"type": "boolean"},
            "feedback": {
                "type": "string",
                "description": "One or two sentences a human can act on.",
            },
            "violations": {
                "type": "array",
                "description": "One entry per departure from the brand. Empty when passed.",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {
                            "type": "string",
                            "description": (
                                "The guardrail id breached, copied exactly from the rule "
                                "list. Required when a rule list was supplied."
                            ),
                        },
                        "field": {
                            "type": "string",
                            "description": "The brand field breached, e.g. visual_identity.",
                        },
                        "expected": {"type": "string", "description": "What the brand asks for."},
                        "observed": {"type": "string", "description": "What the work does."},
                    },
                    "required": ["field", "expected", "observed"],
                },
            },
        },
        "required": ["target", "passed", "feedback"],
    },
}

TOOL_DELEGATE_STORYBOARD = {
    "name": "delegate_to_storyboard",
    "description": "Delegate storyboard creation to the Storyboard Designer agent. Call after submit_concept.",
    "input_schema": {
        "type": "object",
        "properties": {
            "instructions": {
                "type": "string",
                "description": "Extra creative direction for the storyboard agent",
            },
        },
        "additionalProperties": False,
    },
}

TOOL_DELEGATE_MARKETING = {
    "name": "delegate_to_marketing",
    "description": "Delegate marketing copywriting to the Marketing agent. Call after storyboard is ready.",
    "input_schema": {
        "type": "object",
        "properties": {
            "instructions": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

TOOL_RUN_QA = {
    "name": "run_qa_check",
    "description": "Run the QA agent on a generated scene image. Returns pass/fail + feedback.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene_id": {"type": "string"},
        },
        "required": ["scene_id"],
    },
}

TOOL_FINALIZE = {
    "name": "finalize_campaign",
    "description": (
        "Assemble and finalize the campaign once concept, storyboard, copy, and images "
        "(with acceptable QA) are ready."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

# Restored after a merge: 507b6a8 removed this schema while leaving everything
# that needs it in place — crew.py still imports it and still passes it to
# run_art_director_agent, whose submit_direction handler, direction_block and
# fourteen uses of ctx.direction all survived, and agent_steps calls that agent
# from two places. The result was an ImportError on `from ..tools import
# TOOL_SUBMIT_DIRECTION`, so the branch would not start at all. Read back
# verbatim from 3d7e9d8 rather than rewritten, since nothing about the Art
# Director changed — only this schema went missing.
#
# TOOL_DELEGATE_ART_DIRECTOR went the same way in that commit and is NOT
# restored: it is referenced nowhere on the branch, so that removal was
# deliberate and complete.
TOOL_SUBMIT_DIRECTION = {
    "name": "submit_direction",
    "description": "Submit the binding visual direction for the campaign (terminal).",
    "input_schema": {
        "type": "object",
        "properties": {
            "lighting": {
                "type": "string",
                "description": "Key light direction, quality and colour temperature.",
            },
            "colorGrade": {
                "type": "string",
                "description": "Grade and contrast: e.g. warm highlights, lifted shadows, low contrast.",
            },
            "palette": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dominant colours as hex or plain names, most important first.",
            },
            "composition": {
                "type": "string",
                "description": "Camera height, distance, framing and where the product sits.",
            },
            "surfaceAndTexture": {
                "type": "string",
                "description": "Surfaces, materials and backdrop treatment that must recur.",
            },
            "mood": {"type": "string"},
            "notes": {
                "type": "string",
                "description": "Anything later scenes must avoid to stay consistent.",
            },
        },
        "required": ["lighting", "colorGrade", "palette", "composition"],
    },
}


def brand_memory_from_kit(
    brand_kit: dict[str, Any] | None,
    brand_details: BrandDetails | None = None,
) -> str | None:
    """
    What the image provider is told about the brand.

    Visual identity leads when supplied: palette, typography, imagery style and
    composition preferences are precisely what a generator can act on, and until
    now they never left the request — only name, primary colours and archetype
    were forwarded, from the legacy kit.
    """
    parts: list[str] = []

    visual = visual_memory(brand_details)
    if visual:
        parts.append(visual)

    if not brand_kit:
        return ". ".join(parts) if parts else None
    profile = brand_kit.get("profile") or {}
    kit = brand_kit.get("kit") or brand_kit
    name = profile.get("name") or brand_kit.get("name")
    if name:
        parts.append(f"Brand: {name}")
    colors = (kit.get("colors") or {}) if isinstance(kit, dict) else {}
    prim = colors.get("primary") or []
    if prim:
        parts.append(f"Primary colours: {', '.join(str(c) for c in prim[:4])}")
    vibe = kit.get("vibe") or {} if isinstance(kit, dict) else {}
    if isinstance(vibe, dict):
        if vibe.get("archetype"):
            parts.append(f"Archetype: {vibe['archetype']}")
        if vibe.get("description"):
            parts.append(str(vibe["description"])[:400])
    return ". ".join(parts) if parts else None


class CampaignRunContext:
    """Mutable shared state for one campaign run — tools read/write this."""

    def __init__(
        self,
        *,
        idea: str,
        product_images_b64: list[dict[str, str]],
        product_image_urls: list[str],
        brand_kit: dict[str, Any] | None,
        asset_count: int,
        aspect_ratio: str,
        emit: EmitFn,
        brand_details: BrandDetails | dict[str, Any] | None = None,
        project_details: ProjectDetails | dict[str, Any] | None = None,
        hitl_gates: list[str] | None = None,
        approvals: list[Any] | None = None,
        guardrail_set: Any = None,
        escalation_decisions: list[Any] | None = None,
        scene_revisions: list[Any] | None = None,
        product_details: Any = None,
        audience_segment: str | None = None,
        asset_type: str | None = None,
        instructions: str = "",
        text_in_image: bool = False,
    ):
        self.idea = idea
        self.instructions = (instructions or "").strip()
        self.text_in_image = bool(text_in_image)
        self.product_images_b64 = product_images_b64
        self.product_image_urls = product_image_urls
        self.brand_kit = brand_kit
        self.brand_details = coerce_brand(brand_details)
        self.project_details = coerce_project(project_details)
        self.product_details = coerce_product(product_details)
        # What this call is making, so scoped rules can be selected.
        self.audience_segment = (audience_segment or '').strip() or None
        self.asset_type = (asset_type or '').strip() or None
        self.gates = GateSet(hitl_gates)
        self.approvals = Approvals(approvals)
        # Set when a handler refuses to spend. The step surfaces it and returns.
        self.pending_gate: dict[str, Any] | None = None
        self.asset_count = asset_count
        self.aspect_ratio = aspect_ratio or DEFAULT_ASPECT_RATIO
        self._raw_emit = emit
        self.emit = self._emit_with_parent

        self.concept: dict[str, Any] = {}
        self.storyboard: list[dict[str, Any]] = []
        self.copy: dict[str, Any] = {}
        self.assets: dict[str, dict[str, Any]] = {}  # sceneId -> asset
        self.retry_counts: dict[str, int] = {}
        # sceneId -> escalation. Populated when a check has failed MAX_QA_RETRIES + 1
        # times; blocks finalize until a human resolves it.
        # Keyed on (target, scene) so a Guardian escalation and a QA escalation on
        # the same scene both survive. Keyed on the scene alone, the second would
        # silently replace the first and one of the two failures would vanish.
        self.escalations: dict[tuple[str, str], dict[str, Any]] = {}
        self.direction: dict[str, Any] = {}  # binding visual direction from the Art Director
        self.direction_source: str | None = None  # scene the direction was read off
        # Brand-compliance verdicts, keyed on (target, subject_id). Separate from
        # the QA verdicts on assets: the two answer different questions.
        self.guardian_verdicts: dict[tuple[str, str | None], GuardianVerdict] = {}
        # What the Guardian is currently judging. Set by run_guardian_agent so
        # the verdict is filed against the question that was asked.
        self._guardian_scope: tuple[str, str | None] | None = None
        # The rules the Guardian judges against. A set supplied by the caller is
        # used as given — it can carry human amendments that exist nowhere else.
        self.guardrails: GuardrailSet = resolve_guardrails(
            guardrail_set, brand=self.brand_details, project=self.project_details,
            product=self.product_details,
        )
        # Scene id -> what a human asked to change about an image already made.
        # `revision_sources` holds the picture being edited; without one there is
        # nothing to edit and the instruction only steers a fresh generation.
        self.scene_revisions: dict[str, str] = coerce_scene_revisions(scene_revisions)
        self.revision_sources: dict[str, dict[str, str]] = {}
        # Human answers to escalations raised on an earlier call.
        self.escalation_decisions = EscalationDecisions(escalation_decisions)
        # Checks a human overruled, kept so the response can say which work
        # shipped over a failed verdict rather than passing one.
        self.overrides: list[dict[str, Any]] = []
        self._apply_rule_amendments()
        self.finalized = False
        self.needs_human_review = False
        self.guardrail_set: dict[str, Any] | None = None
        self.product_agent_output: dict[str, Any] | None = None

    async def _emit_with_parent(self, event: dict[str, Any]) -> None:
        """
        Stamp data.parent on events this context emits.

        The runtime sets parent on its own events, but handlers emit their own —
        image_generation, storyboard_ready, provider errors — and those arrived
        without it, so a viewer rendered them as roots beside the supervisor
        rather than as work done inside it.
        """
        active = current_agent()
        if active and isinstance(event, dict):
            data = event.get("data")
            if not isinstance(data, dict):
                data = {}
                event["data"] = data
            data.setdefault("parent", active)
        await self._raw_emit(event)

    def brand_block(self, role: str | None = None) -> str:
        """
        The brand and project context this role should see.

        Typed details win when present: they are the field set the caller
        actually filled in, sliced per role so an agent is not handed context
        belonging to a different job. A legacy brand_kit dict still works and
        renders as before, so nothing calling without a role changes behaviour.

        Everything is tagged. These values are user-written or scraped, and each
        agent prompt declares them untrusted — the tags are what that
        declaration points at.
        """
        parts: list[str] = []

        if (self.brand_details is not None or self.project_details is not None
                or self.product_details is not None):
            rendered = render_context(
                brand=self.brand_details,
                project=self.project_details,
                product=self.product_details,
                role=role or "",
            )
            if rendered:
                parts.append(rendered)

        if self.brand_kit:
            body = json.dumps(self.brand_kit, indent=2, default=str)[:4000]
            parts.append(f"<brand_kit>\n{body}\n</brand_kit>")

        if not parts:
            return "No brand context provided. Use a clean modern commercial aesthetic."
        return "\n".join(parts)

    def direction_block(self) -> str:
        """Render the locked visual direction for injection into later scene prompts."""
        if not self.direction:
            return ""
        parts = [
            f"Lighting: {self.direction.get('lighting')}",
            f"Colour grade: {self.direction.get('colorGrade')}",
            f"Composition: {self.direction.get('composition')}",
        ]
        palette = self.direction.get("palette") or []
        if palette:
            parts.append(f"Palette: {', '.join(str(c) for c in palette)}")
        for key, label in (("surfaceAndTexture", "Surfaces"), ("mood", "Mood"), ("notes", "Avoid")):
            if self.direction.get(key):
                parts.append(f"{label}: {self.direction[key]}")
        return "\n".join(parts)

    # ── tool handlers ────────────────────────────────────────────────────────

    async def view_product_images(self, _args: dict[str, Any]) -> dict[str, Any]:
        """Return metadata; actual images are injected as multimodal by the agent wrappers."""
        count = len(self.product_images_b64)
        return {
            "count": count,
            "message": f"{count} product reference image(s) available. They are attached for you to view.",
            "mimes": [img.get("mime_type") for img in self.product_images_b64[:4]],
            "__images__": [
                image_block(img.get("mime_type") or "image/jpeg", img["data"])
                for img in self.product_images_b64[:4]
                if img.get("data")
            ],
        }

    async def view_generated_image(self, args: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(args.get("scene_id") or "")
        asset = self.assets.get(scene_id)
        if not asset or not asset.get("imageBase64"):
            return {"error": f"No generated image for {scene_id}", "__images__": []}
        return {
            "sceneId": scene_id,
            "title": asset.get("title"),
            "retries": asset.get("retries", 0),
            "qa": asset.get("qa"),
            "__images__": [
                image_block(asset.get("mimeType") or "image/png", asset["imageBase64"])
            ],
        }

    async def generate_scene_image(self, args: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(args.get("scene_id") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        title = args.get("title")
        aspect = str(args.get("aspect_ratio") or self.aspect_ratio)

        if not scene_id or not prompt:
            return {"error": "scene_id and prompt are required"}

        # The locked direction is injected verbatim into every scene prompt, so
        # approving it after the images exist would approve nothing — the money is
        # already spent. Check it here, at the first attempt to spend, and one
        # approval then covers the whole campaign.
        if (
            self.direction
            and self.gates.is_gated(GATE_DIRECTION)
            and not self.approvals.approved(GATE_DIRECTION)
        ):
            return await self.raise_gate(
                gate=GATE_DIRECTION,
                subject_id=None,
                payload={
                    "direction": self.direction,
                    # The direction was read off an image; say which, so the human
                    # can look at it rather than judging the words alone.
                    "read_from_scene_id": self.direction_source,
                    "blocked_scene_id": scene_id,
                },
                message=(
                    "Waiting for approval of the visual direction — it shapes every "
                    "remaining scene"
                ),
            )

        # A human read the last prompt for this scene and turned it down. Fold
        # their requirement into the prompt and put it straight back in front of
        # them — they approve the revision or edit it further.
        #
        # This deliberately does not ask an agent to rewrite it. Some callers of
        # this handler are agents and some are plain loops (step_scene_images is
        # a loop), and a handler cannot tell which. Returning "rewrite this" to a
        # loop would skip the scene silently, which is the one outcome a rejection
        # must never produce. Revising here works either way, and it terminates:
        # the next decision on record for this scene replaces this one.
        rejection = self.approvals.retry_feedback(GATE_PRE_IMAGE, scene_id)
        if rejection:
            revised = f"{prompt}\n\nHUMAN REVIEW — required changes:\n{rejection}"
            return await self.raise_gate(
                gate=GATE_PRE_IMAGE,
                subject_id=scene_id,
                payload={
                    "scene_id": scene_id,
                    "title": title,
                    "prompt": revised,
                    "aspect_ratio": aspect,
                    "rejected_feedback": rejection,
                    "attempt": int(self.retry_counts.get(scene_id, 0)) + 1,
                },
                message=(
                    f"Prompt for {scene_id} revised with your feedback — approve it to generate"
                ),
            )

        # The gate lives here, ahead of the provider call, rather than in the
        # prompt. An agent that forgets to ask cannot spend anyway: without an
        # approval on record the generate call below is unreachable.
        if self.gates.is_gated(GATE_PRE_IMAGE, scene_id) and not self.approvals.approved(
            GATE_PRE_IMAGE, scene_id
        ):
            return await self.raise_gate(
                gate=GATE_PRE_IMAGE,
                subject_id=scene_id,
                payload={
                    "scene_id": scene_id,
                    "title": title,
                    "prompt": prompt,
                    "aspect_ratio": aspect,
                    "attempt": int(self.retry_counts.get(scene_id, 0)) + 1,
                },
                message=(
                    f"Approval needed before generating {scene_id}. Do not call "
                    "generate_scene_image for this scene again; continue with other work "
                    "or stop."
                ),
            )

        # Whatever the human approved wins over what the agent just drafted —
        # edited or not. Resuming re-runs the agent, and it is free to write a
        # different prompt than the one that was on screen; generating that would
        # spend on text nobody approved. Falls back to the agent's prompt when the
        # caller approved without echoing a payload.
        approved = self.approvals.approved_payload(GATE_PRE_IMAGE, scene_id) or {}
        if approved.get("prompt"):
            prompt = str(approved["prompt"]).strip()
        if approved.get("aspect_ratio"):
            aspect = str(approved["aspect_ratio"])

        attempt = int(self.retry_counts.get(scene_id, 0))

        # A revision replaces the brief rather than extending it. The scene's own
        # prompt describes a picture to make; what is wanted now is a change to
        # the picture that exists, and the two read as contradictory instructions
        # if both are sent.
        revision_text = self.scene_revisions.get(scene_id)
        if revision_text and self.revision_sources.get(scene_id):
            prompt = render_image_revision(revision_text)
        elif revision_text:
            # No image to edit — fall back to steering a fresh generation, and say
            # so rather than pretending an edit happened.
            prompt = f"{prompt}\n\nREQUESTED CHANGE:\n{revision_text}"

        # Once the Art Director has locked a direction, every later scene is generated
        # under it. This is what keeps scene 4 looking like scene 1.
        direction = self.direction_block()
        if direction and not (revision_text and self.revision_sources.get(scene_id)):
            prompt = (
                f"{prompt}\n\nLOCKED VISUAL DIRECTION. Match this exactly so the scene "
                f"is consistent with the rest of the campaign:\n{direction}"
            )

        # Appended here rather than left to the scene's own prompt, for the
        # reason the gates live here: a rule enforced in the handler cannot be
        # forgotten by whatever wrote the prompt. The Storyboard Designer is
        # told to keep words out of an imagePrompt, and it has written "a
        # footer strip carries the price stack (₹749, struck-through ₹1799,
        # 58% OFF)" anyway — a scene description that asks for type, four
        # figures a caption is separately checked against, and a typeface the
        # generator will guess at. Last in the prompt so it also covers a
        # revision, which replaces everything above it.
        if not self.wants_text_in_image(scene_id):
            prompt = f"{prompt}\n\n{self.NO_TEXT_RULE}"

        # Inject last QA feedback on retries
        prev = self.assets.get(scene_id) or {}
        feedback = (prev.get("qa") or {}).get("feedback")
        if attempt > 0 and feedback:
            prompt = (
                f"{prompt}\n\nQA FEEDBACK — fix these issues while keeping the exact "
                f"product from the reference photos:\n{feedback}"
            )

        # Which picture the provider edits. Normally the product photo, so the
        # scene is built around the real object. On a revision it is the asset
        # already on screen: a human who said "make it darker" meant that image,
        # and starting again from the product photo returns a different shot that
        # merely happens to be darker.
        # A revision edits one specific picture, so it stays single-image: the
        # asset on screen, not blended with product photos it was never asked
        # to reconsider. A fresh scene is built from every photo the caller
        # supplied — a front-only reference is what made a generated back view
        # a guess rather than a fact, and product_images_b64 already carries
        # whichever photos this call actually has.
        revising = self.scene_revisions.get(scene_id)
        source = self.revision_sources.get(scene_id) if revising else None
        image_b64 = mime = None
        source_images = None
        if source:
            image_b64, mime = source.get("data"), source.get("mime_type") or "image/png"
        else:
            source_images = self.product_images_b64 or None

        await self.emit(
            {
                "type": "agent_message",
                "agent": "creative_director",
                "message": f"Generating image for {scene_id} (attempt {attempt + 1})",
                "data": {"sceneId": scene_id, "attempt": attempt + 1},
            }
        )

        try:
            result = await generate_image(
                ImageGenerationRequest(
                    prompt=prompt,
                    image_base64=image_b64,
                    image_mime_type=mime or "image/jpeg",
                    source_images=source_images,
                    aspect_ratio=aspect,
                    brand_memory=brand_memory_from_kit(self.brand_kit, self.brand_details),
                )
            )
        except Exception as exc:
            log.error("generate_scene_image failed: %s", exc, exc_info=True)
            self.assets[scene_id] = {
                "sceneId": scene_id,
                "title": title,
                "prompt": prompt,
                "imageBase64": None,
                "mimeType": None,
                "retries": attempt,
                "qa": {"passed": False, "feedback": str(exc), "error": True},
                "error": str(exc),
            }
            await self.emit(
                {
                    "type": "error",
                    "agent": "image_generation",
                    "message": f"Image generation failed for {scene_id}: {exc}",
                }
            )
            return {"ok": False, "sceneId": scene_id, "error": str(exc)}

        asset = {
            "sceneId": scene_id,
            "title": title,
            "prompt": prompt,
            "imageBase64": result.image_base64,
            "mimeType": result.mime_type,
            "retries": attempt,
            "qa": None,
        }
        self.assets[scene_id] = asset

        await self.emit(
            {
                "type": "asset_generated",
                "agent": "image_generation",
                "message": f"Image generated for {scene_id}",
                "data": {
                    "sceneId": scene_id,
                    "mimeType": result.mime_type,
                    "imageBase64": result.image_base64,
                    "retries": attempt,
                    "title": title,
                },
            }
        )
        return {
            "ok": True,
            "sceneId": scene_id,
            "mimeType": result.mime_type,
            "retries": attempt,
            "message": "Image generated successfully. Run QA next.",
        }

    async def submit_concept(self, args: dict[str, Any]) -> dict[str, Any]:
        shot = int(args.get("shotCount") or self.asset_count)
        shot = max(1, min(shot, self.asset_count))
        concept = {
            "theme": args.get("theme"),
            "tone": args.get("tone"),
            "targetAudience": args.get("targetAudience"),
            "visualStyle": args.get("visualStyle"),
            "shotCount": shot,
            "summary": args.get("summary"),
        }
        self.concept = concept
        await self.emit(
            {
                "type": "agent_message",
                "agent": "creative_director",
                "message": concept.get("summary") or f"Concept ready: {concept.get('theme')}",
                "data": {"concept": concept},
            }
        )
        return terminal({"ok": True, "concept": concept})

    async def submit_storyboard(self, args: dict[str, Any]) -> dict[str, Any]:
        scenes_raw = args.get("scenes") or []
        if not isinstance(scenes_raw, list) or not scenes_raw:
            return {"error": "scenes array is required"}
        shot = int(self.concept.get("shotCount") or self.asset_count)
        normalized = []
        for scene in scenes_raw[:shot]:
            if not isinstance(scene, dict):
                continue
            # Assigned by position, not taken from the agent.
            #
            # A scene id is referred to before it exists. The planner writes
            # params.sceneIds when it splits one storyboard across two
            # branches, and it does that on an earlier call — this agent names
            # the scenes on a later one. Left to its own choice it called them
            # scene-1 and scene-2 while the planner had asked for scene_1, and
            # the image node failed outright: "None of the requested scene_ids
            # (['scene_1']) are in the storyboard".
            #
            # Numbering them here makes the two sides agree by construction
            # rather than by luck, and gives one predictable form to everything
            # else that names a scene: a per-scene gate (post_image:scene_2),
            # qa_check's sceneId, video_director's cut list, a human typing one
            # into the console. The agent's own id is dropped rather than kept
            # alongside — `title` already carries what it meant, and a second
            # id nothing resolves against is the thing that caused this.
            #
            # Counted off `normalized` rather than enumerate(): a non-dict in
            # the list used to consume its index and leave a gap, so a bad
            # element produced scene-1 and scene-3 with no scene-2.
            normalized.append({**scene, "id": f"scene_{len(normalized) + 1}"})
        if not normalized:
            return {"error": "No valid scenes"}
        self.storyboard = normalized
        await self.emit(
            {
                "type": "storyboard_ready",
                "agent": "storyboard_designer",
                "message": f"Storyboard ready with {len(normalized)} scenes",
                "data": {"scenes": normalized},
            }
        )
        return terminal({"scenes": normalized})

    async def submit_copy(self, args: dict[str, Any]) -> dict[str, Any]:
        copy = {
            "headline": args.get("headline"),
            "tagline": args.get("tagline"),
            "cta": args.get("cta"),
            "instagramCaption": args.get("instagramCaption"),
            "adBannerText": args.get("adBannerText"),
            "sceneCaptions": args.get("sceneCaptions") or [],
        }
        self.copy = copy
        await self.emit(
            {
                "type": "copy_ready",
                "agent": "marketing",
                "message": copy.get("headline") or "Campaign copy ready",
                "data": {"copy": copy},
            }
        )
        return terminal({"copy": copy})

    async def submit_verdict(self, args: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(args.get("scene_id") or "")
        passed = bool(args.get("passed"))
        score = int(args.get("score") or 0)
        if score >= 75:
            passed = True
        feedback = str(args.get("feedback") or ("Looks good" if passed else "Needs regeneration"))
        verdict = {
            "passed": passed,
            "score": score,
            "productFidelity": args.get("productFidelity"),
            "brandAlignment": args.get("brandAlignment"),
            "briefConsistency": args.get("briefConsistency"),
            "feedback": feedback,
        }
        if scene_id in self.assets:
            self.assets[scene_id]["qa"] = verdict

        await self.emit(
            {
                "type": "qa_verdict",
                "agent": "qa",
                "message": f"QA {'passed' if passed else 'failed'} for {scene_id} (score {score})",
                "data": {
                    "sceneId": scene_id,
                    "passed": passed,
                    "score": score,
                    "feedback": feedback,
                    "verdict": verdict,
                },
            }
        )
        return terminal({"verdict": verdict, "sceneId": scene_id})

    async def submit_guardian_verdict(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Record the brand-compliance verdict for one target.

        Kept out of `assets[sid]["qa"]` deliberately: a caller reading the QA
        verdict must not find brand failures folded into it, and vice versa.
        """
        # Routing comes from what the Guardian was ASKED to judge, not from what
        # it says it judged. The model's arguments describe the verdict; letting
        # them pick the storage key means a model that echoes the target loosely
        # — or invents a subject_id — files the verdict where the step never
        # looks, and the step reports "no verdict" for a check that did run.
        scope = self._guardian_scope
        declared = str(args.get("target") or "").strip()
        if scope is not None:
            target, subject_id = scope
            if declared and declared != target:
                log.warning(
                    "guardian declared target=%r while judging %r; using %r",
                    declared, target, target,
                )
        else:
            target = declared
            subject_id = str(args.get("subject_id") or "").strip() or None

        if target not in ALL_TARGETS:
            return {
                "error": (
                    f"Unknown target {target!r}. Use one of: {', '.join(ALL_TARGETS)}."
                )
            }
        passed = bool(args.get("passed"))

        raw_violations = args.get("violations")
        violations: list[Violation] = []
        unknown_rules: list[str] = []
        for item in raw_violations if isinstance(raw_violations, list) else []:
            if not isinstance(item, dict) or not str(item.get("field") or "").strip():
                continue
            rule_id = str(item.get("rule_id") or "").strip() or None
            # A rule id that is not in the set points at nothing a human can
            # amend, so it is worse than no id at all. Collect and push back.
            if rule_id and self.guardrails.get(rule_id) is None:
                unknown_rules.append(rule_id)
            violations.append(
                Violation(
                    rule_id=rule_id,
                    field=str(item["field"]).strip(),
                    expected=str(item.get("expected") or "").strip(),
                    observed=str(item.get("observed") or "").strip(),
                )
            )

        if unknown_rules:
            known = ", ".join(self.guardrails.ids()) or "(none)"
            return {
                "error": (
                    f"Unknown rule_id(s): {', '.join(unknown_rules)}. Use an id from the "
                    f"rule list exactly as written, or omit rule_id. Known ids: {known}."
                )
            }

        feedback = str(args.get("feedback") or "").strip()
        if not passed and not feedback and not violations:
            # A failure with nothing to act on cannot drive a retry and cannot be
            # reviewed. Push back rather than storing a dead end.
            return {
                "error": (
                    "A failing verdict needs a reason. Give feedback, or violations "
                    "naming the brand field, what was expected and what you observed."
                )
            }

        verdict = GuardianVerdict(
            target=target,
            subject_id=subject_id,
            passed=passed,
            feedback=feedback or ("On brand" if passed else "Off brand"),
            violations=violations,
        )
        self.guardian_verdicts[(target, subject_id)] = verdict
        if target == TARGET_IMAGE and subject_id in self.assets:
            self.assets[subject_id]["guardian"] = verdict.model_dump()

        await self.emit(
            {
                "type": "guardian_verdict",
                "agent": "guardian",
                "message": (
                    f"Guardian {'passed' if passed else 'failed'} {target}"
                    + (f" for {subject_id}" if subject_id else "")
                    + (f" — {len(violations)} violation(s)" if violations else "")
                ),
                "data": verdict.model_dump(),
            }
        )
        return terminal({"verdict": verdict.model_dump()})

    def guardian_verdict(
        self, target: str, subject_id: str | None = None
    ) -> GuardianVerdict | None:
        return self.guardian_verdicts.get((target, subject_id))

    def _apply_rule_amendments(self) -> None:
        """
        Rewrite the guardrails a human corrected, before any check reads them.

        Applied at construction so the amended rule is what the Guardian is shown
        this call, not next call. An amendment that arrived and then failed the
        same work against the old wording would look like the system ignoring it.

        Replaces in place and keeps the rule's position, so a set stays in the
        order the caller has been rendering. Marked `human`: it is no longer
        derived from the brand field it started as, and re-deriving must not
        quietly restore the original.
        """
        amendments = self.escalation_decisions.amendments()
        if not amendments:
            return

        rules = list(self.guardrails.rules)
        by_id = {rule.id: index for index, rule in enumerate(rules)}
        for amendment in amendments:
            replacement = GuardrailRule(
                id=amendment.id,
                rule=amendment.rule,
                source=SOURCE_HUMAN,
                scope=amendment.scope or SCOPE_CAMPAIGN,
                applies_to=amendment.applies_to,
            )
            if amendment.id in by_id:
                rules[by_id[amendment.id]] = replacement
            else:
                # A human may add a rule the derivation never produced.
                rules.append(replacement)
        self.guardrails = GuardrailSet(version=self.guardrails.version, rules=rules)

    def active_guardrails(self) -> GuardrailSet:
        """
        The rules that apply to what this call is making.

        A view over `self.guardrails`, never a replacement. The full set is what
        the response returns and the caller round-trips; narrowing the stored one
        would delete every other segment's rules the first time one segment ran.
        """
        return self.guardrails.scoped_to(
            segment=self.audience_segment, asset_type=self.asset_type
        )

    def logo_image(self) -> dict[str, str] | None:
        """The brand's mark as bytes, or None when none was supplied."""
        logo = getattr(self.brand_details, "logo", None)
        return logo.viewable() if logo is not None else None

    # Words that mean "words in the frame", when a person writes them as
    # direction. Deliberately not matched against `idea`: the default brief in
    # the console says "an Instagram post that mentions the price and the
    # discount", which is about the caption marketing_copy writes, not about
    # printing ₹749 onto the photograph. Reading the campaign brief this way
    # would switch text on for almost every campaign and make the default
    # meaningless.
    _TEXT_WORDS = re.compile(
        r"\b(text|type|typograph\w*|caption|headline|tagline|slogan|lettering|"
        r"wordmark|logo|words?|wording|label|badge|sticker|overlay|watermark|"
        r"sign|signage|subtitle|cta|call.to.action|print(?:ed)?\s+(?:text|price)|"
        r"price\s+(?:tag|badge|sticker|stack|card)|%\s*off)\b",
        re.IGNORECASE,
    )

    # A figure AND somewhere to put it. "Put the price on the shot" is a
    # request for type; bare "price" is not, which is why it is absent from the
    # list above. Both halves are required so that "keep the product centred in
    # the frame" — a placement phrase with nothing to set — does not switch
    # type on, since a false yes here is the expensive direction: it permits
    # exactly what this exists to prevent.
    _TEXT_PLACED = re.compile(
        r"\b(?:price|prices|offer|discount|mrp|saving|deal)\b[^.]{0,40}?"
        r"\b(?:on|onto|in|across|over|at)\s+(?:the\s+)?"
        r"(?:shot|shots|image|images|picture|photo|photos|creative|creatives|"
        r"frame|asset|assets|visual|visuals|poster|banner|top|bottom|corner)\b",
        re.IGNORECASE,
    )

    def wants_text_in_image(self, scene_id: str | None = None) -> bool:
        """
        Whether words may be rendered into the picture.

        No by default, and that is the point: left to itself a generator writes
        a tagline nobody approved, a price that contradicts the offer, or a
        watermark in a script it cannot spell. Yes only when someone said so —
        the flag a caller sets, or direction they typed, either for the node or
        for the one scene being revised.
        """
        if self.text_in_image:
            return True
        asked = [self.instructions]
        if scene_id:
            asked.append(self.scene_revisions.get(scene_id) or "")
        return any(
            self._TEXT_WORDS.search(text) or self._TEXT_PLACED.search(text)
            for text in asked if text
        )

    # The instruction itself, so the wording lives in one place rather than
    # being rebuilt at each call site that needs it.
    #
    # Ordered keep-first after watching an earlier draft fail both ways on one
    # render. It led with "NO TEXT IN THE IMAGE" and merely permitted the
    # product's own lettering; the model read the ban as dominant and returned a
    # blank cream hoodie with the printed graphic gone — text removed by
    # changing the product, which is worse than the problem. The same render
    # also copied an "OVERSIZED FIT" badge, a magnifier icon and carousel dots
    # out of the reference, because the reference is a listing-page screenshot
    # and "lettering that belongs to the product" was read to include the page
    # around it. So: what to keep, then what not to add, then what not to copy.
    NO_TEXT_RULE = (
        "TYPE IN THIS IMAGE — three rules, in this order.\n"
        "1. KEEP the artwork already printed on the product itself exactly as "
        "it appears in the reference photographs — every graphic, letterform, "
        "label and mark on the garment, packaging or object. That artwork IS "
        "the product. Removing it, blanking it, simplifying it or redrawing it "
        "is a worse failure than any of the below, because it returns a "
        "different product.\n"
        "2. ADD no type of your own anywhere in the frame: no words, numerals, "
        "prices, discounts, taglines, captions, labels, badges, stickers, "
        "watermarks, signage, logos or overlays, and nothing added to the "
        "product's own printed artwork. Nobody asked for type in this asset, "
        "and type a generator sets on its own is unapproved and usually "
        "misspelled.\n"
        "3. COPY no type that sits over or beside the product in the reference "
        "rather than on it — a listing page's badges, price flashes, interface "
        "icons, arrows, carousel dots, favourite hearts, watermarks or borders. "
        "Those belong to the photograph, not to the product. Leave that space "
        "clean — and do not relocate any of it onto the product either. Gone "
        "means gone, not moved: a heart from a listing page reappearing as a "
        "print on the garment is the same error wearing a different hat."
    )

    def has_logo(self) -> bool:
        return self.logo_image() is not None

    async def submit_direction(self, args: dict[str, Any]) -> dict[str, Any]:
        palette_raw = args.get("palette") or []
        palette = [str(c) for c in palette_raw if str(c).strip()] if isinstance(palette_raw, list) else []

        # This gets locked once and injected into every later scene's prompt verbatim
        # (see direction_block). A blank required field here would silently poison the
        # rest of the campaign's prompts, so reject and force a retry instead of locking it.
        missing = [
            field
            for field, value in (
                ("lighting", args.get("lighting")),
                ("colorGrade", args.get("colorGrade")),
                ("composition", args.get("composition")),
            )
            if not str(value or "").strip()
        ]
        if not palette:
            missing.append("palette")
        if missing:
            return {
                "error": (
                    f"Missing or empty required field(s): {', '.join(missing)}. "
                    "Look at the generated image again and resubmit with lighting, "
                    "colorGrade, composition and palette all filled in."
                )
            }

        direction = {
            "lighting": args.get("lighting"),
            "colorGrade": args.get("colorGrade"),
            "palette": palette,
            "composition": args.get("composition"),
            "surfaceAndTexture": args.get("surfaceAndTexture"),
            "mood": args.get("mood"),
            "notes": args.get("notes"),
        }
        # If a human already approved a direction at the gate, that is the one the
        # campaign uses. The agent runs again on every resumed request and would
        # otherwise be free to submit something the human never saw.
        approved = self.approvals.approved_payload(GATE_DIRECTION) or {}
        pinned = approved.get("direction") if isinstance(approved.get("direction"), dict) else None
        if pinned:
            direction = {**direction, **{k: v for k, v in pinned.items() if v}}

        self.direction = direction

        await self.emit(
            {
                "type": "direction_ready",
                "agent": "art_director",
                "message": (
                    f"Visual direction locked: {direction.get('lighting')} / "
                    f"{direction.get('colorGrade')}"
                ),
                "data": {"direction": direction},
            }
        )
        return terminal({"direction": direction})

    async def finalize_campaign(self, args: dict[str, Any]) -> dict[str, Any]:
        # An escalated scene is one a check rejected past its retry budget. Finalizing
        # over the top of it is how rejected assets used to reach finished campaigns,
        # so this is a hard stop rather than a warning. Not terminal: the agent stays
        # in its loop and can keep working on anything else.
        open_escalations = self.open_escalations()
        if open_escalations:
            # Name the check as well as the scene: "qa rejected scene_001" and
            # "guardian rejected scene_001" are different problems with different
            # fixes, and both can be open at once.
            listed = ", ".join(
                f"{e.get('target')} on {e.get('sceneId')}" for e in open_escalations
            )
            return {
                "error": (
                    f"Cannot finalize — {len(open_escalations)} item(s) are awaiting human "
                    f"review: {listed}. These were rejected past the retry limit and cannot "
                    "be accepted automatically."
                ),
                "escalations": open_escalations,
            }

        assets_list = []
        for scene in self.storyboard:
            sid = scene.get("id")
            asset = self.assets.get(sid) if sid else None
            if asset and asset.get("imageBase64"):
                assets_list.append(asset)
        # Also include any orphan assets
        for sid, asset in self.assets.items():
            if asset.get("imageBase64") and not any(a.get("sceneId") == sid for a in assets_list):
                assets_list.append(asset)

        summary = str(args.get("summary") or f"Campaign complete with {len(assets_list)} assets")
        self.finalized = True

        payload = {
            "concept": self.concept,
            "storyboard": self.storyboard,
            "copy": self.copy,
            "assets": [
                {
                    "sceneId": a.get("sceneId"),
                    "title": a.get("title"),
                    "mimeType": a.get("mimeType"),
                    "retries": a.get("retries", 0),
                    "qa": a.get("qa"),
                    "hasImage": bool(a.get("imageBase64")),
                    "imageBase64": a.get("imageBase64"),
                    "prompt": a.get("prompt"),
                }
                for a in assets_list
            ],
            "summary": summary,
        }
        await self.emit(
            {
                "type": "campaign_complete",
                "agent": "creative_director",
                "message": summary,
                "data": payload,
            }
        )
        return terminal(payload)

    def can_retry(self, scene_id: str) -> bool:
        return int(self.retry_counts.get(scene_id, 0)) < MAX_QA_RETRIES

    def bump_retry(self, scene_id: str) -> int:
        n = int(self.retry_counts.get(scene_id, 0)) + 1
        self.retry_counts[scene_id] = n
        return n

    async def raise_escalation(
        self,
        *,
        subject: str,
        target: str,
        verdict: dict[str, Any],
        attempts: int,
    ) -> dict[str, Any]:
        """
        Record that a check has exhausted its retries and needs a person.

        Replaces the previous accept-and-continue behaviour: the work stays
        unapproved and finalize_campaign refuses until this is resolved.

        `subject` is whatever was judged — a scene for image checks, a stage name
        like "concept" for the Guardian's text targets. `sceneId` is filled in
        only when the subject really is a scene, because a caller that renders
        escalations against a storyboard should get nothing rather than a scene
        that does not exist.
        """
        # A human already looked at this failure and chose to ship anyway. Record
        # the override and do not block: an escalation that stays open after
        # being answered is the same dead end as one that could not be answered.
        override = self.escalation_decisions.accepted(target, subject)
        if override is not None:
            entry = {
                "target": target,
                "subject": subject,
                "reason": override.reason,
                "actor": override.actor,
                "verdict": verdict,
                "attempts": attempts,
            }
            self.overrides.append(entry)
            await self.emit(
                {
                    "type": "escalation_overridden",
                    "agent": target,
                    "message": (
                        f"{target} rejected {subject}, accepted anyway by "
                        f"{override.actor or 'a human'}: {override.reason}"
                    ),
                    "data": entry,
                }
            )
            return entry

        is_scene = subject in self.assets or any(
            s.get("id") == subject for s in self.storyboard
        )
        escalation = {
            "subject": subject,
            "sceneId": subject if is_scene else None,
            "target": target,
            "attempts": attempts,
            "verdict": verdict,
            "feedback": verdict.get("feedback"),
            "options": ["regenerate", "accept_anyway", "amend_rule"],
        }
        self.escalations[(target, subject)] = escalation

        await self.emit(
            {
                "type": "escalation_raised",
                "agent": target,
                "message": (
                    f"{target} rejected {subject} {attempts} times — needs human review"
                ),
                "data": escalation,
            }
        )
        return escalation

    def open_escalations(self) -> list[dict[str, Any]]:
        return list(self.escalations.values())

    async def raise_gate(
        self,
        *,
        gate: str,
        subject_id: str | None,
        payload: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        """
        Record that work stopped for a human, and tell the agent to stand down.

        A step returns one question, so the first gate wins — a second subject's
        gate would just be noise the caller has to reconcile. The exception is a
        gate for the subject already pending: that is the same question being
        re-asked with a newer proposal, usually because the human rejected the
        last prompt and the agent wrote another. Keeping the stale one would show
        the human the prompt they already turned down.
        """
        block = gate_payload(gate=gate, subject_id=subject_id, payload=payload)
        open_gate = self.pending_gate
        same_question = (
            open_gate is not None
            and open_gate.get("gate") == gate
            and open_gate.get("subject_id") == subject_id
        )
        if open_gate is None or same_question:
            self.pending_gate = block
            await self.emit(
                {
                    "type": "awaiting_approval",
                    "agent": current_agent() or "system",
                    "message": message or f"Waiting for approval at {gate}",
                    "data": block,
                }
            )
        return {
            "awaiting_approval": True,
            "gate": gate,
            "subject_id": subject_id,
            "message": message or f"Waiting for approval at {gate}",
        }

    def gate_for(
        self,
        gate: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        A stage gate the step should report, or None to continue.

        `payload` is what the human is being asked to approve. Pass it: a gate
        with an empty payload asks someone to approve something they cannot see.
        """
        if not self.gates.is_gated(gate, subject_id):
            return None
        if self.approvals.approved(gate, subject_id):
            return None
        return gate_payload(gate=gate, subject_id=subject_id, payload=payload)
