"""
video_tools.py — Tools for the Video Director agent.

Per-scene Veo clips are generated from campaign creatives, then stitched
into a single campaign film with ffmpeg.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from typing import Any

from ..creative_studio.config import (
    SUPPORTED_ASPECT_RATIOS_VIDEO,
    SUPPORTED_VIDEO_DURATIONS_SECONDS,
    VIDEO_POLL_INTERVAL_SECONDS,
    VIDEO_POLL_MAX_ATTEMPTS,
)
from ..creative_studio.gemini import (
    VideoGenerationRequest,
    poll_video_operation,
    submit_video_generation,
)
from ..agent_core.brand_context import (
    VIDEO_DIRECTOR,
    coerce_brand,
    coerce_product,
    coerce_project,
    context_block,
)
from ..agent_core.escalations import EscalationDecisions
from ..agent_core.revision import coerce_scene_revisions
from ..agent_core.gates import (
    GATE_PRE_VIDEO,
    Approvals,
    GateSet,
    gate_payload,
)
from ..agent_core.guardrails import GuardrailSet, resolve as resolve_guardrails
from . import clip_store
from .agent_runtime import EmitFn, terminal
from .logger import get_logger
from .tools import brand_memory_from_kit

log = get_logger(__name__)

TOOL_GENERATE_SCENE_CLIP = {
    "name": "generate_scene_clip",
    "description": (
        "Generate one cut of the campaign film from a storyboard scene. Call it once "
        "per scene you want in the film — finalize_video stitches them in order. The "
        "start frame is that scene's own generated hero image when one exists, else "
        "the product photo; the end frame, when available, is the next scene's image, "
        "so consecutive cuts flow into each other. Write a cinematic motion_prompt "
        "covering that scene's action, camera move and pacing — describe motion, not "
        "the picture itself, since the picture is already decided."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scene_id": {
                "type": "string",
                "description": "Must match the customer-selected scene id",
            },
            "motion_prompt": {
                "type": "string",
                "description": "Detailed cinematic motion / camera / pacing prompt",
            },
            "duration_seconds": {
                "type": "integer",
                "description": (
                    "Length of THIS cut. Vary it across cuts for pacing; the film is "
                    "the cuts stitched together."
                ),
                "enum": sorted(SUPPORTED_VIDEO_DURATIONS_SECONDS),
            },
        },
        "required": ["scene_id", "motion_prompt"],
    },
}

TOOL_FINALIZE_VIDEO = {
    "name": "finalize_video",
    "description": (
        "Stitch all generated scene clips into one campaign film in storyboard order "
        "and deliver the final video. Call only after every scene has a clip."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Scene ids in playback order (usually storyboard order)",
            },
            "summary": {
                "type": "string",
                "description": "Short description of the finished campaign film",
            },
        },
        "required": ["order"],
    },
}


def _coerce_video_aspect(aspect: str | None) -> str:
    if aspect in SUPPORTED_ASPECT_RATIOS_VIDEO:
        return aspect  # type: ignore[return-value]
    return "16:9"


def _coerce_duration(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 8
    if n not in SUPPORTED_VIDEO_DURATIONS_SECONDS:
        return 8
    return n


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    """Return (bytes, mime) from a data:{mime};base64,... URL or raw base64."""
    if data_url.startswith("data:") and "," in data_url:
        header, b64 = data_url.split(",", 1)
        mime = "video/mp4"
        if ";" in header:
            mime = header[5:].split(";", 1)[0] or "video/mp4"
        return base64.b64decode(b64), mime
    return base64.b64decode(data_url), "video/mp4"


async def _stitch_clips_ffmpeg(clip_paths: list[str], out_path: str) -> None:
    """Concat clips with re-encode for safe cross-clip stitching."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is not installed. Install ffmpeg (e.g. brew install ffmpeg) "
            "and restart the AI service."
        )

    list_path = out_path + ".txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            # ffmpeg concat demuxer requires escaped single quotes in paths
            safe = p.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    async def _run(cmd: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        err = (stderr or b"").decode("utf-8", errors="replace")[-2000:]
        return proc.returncode or 0, err

    # Prefer stream copy when codecs match; fall back to re-encode.
    code, err = await _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            out_path,
        ]
    )
    if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
        return

    # Re-encode video; include AAC audio when present, otherwise silent video.
    code, err = await _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            out_path,
        ]
    )
    if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
        return

    code, err = await _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            out_path,
        ]
    )
    if code != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        raise RuntimeError(f"ffmpeg stitch failed (code {code}): {err}")


class VideoRunContext:
    """Shared state for one Video Director run — one or more cuts, stitched."""

    def __init__(
        self,
        *,
        concept: dict[str, Any],
        storyboard: list[dict[str, Any]],
        copy: dict[str, Any],
        brand_kit: dict[str, Any] | None,
        instructions: str,
        aspect_ratio: str,
        selected_scene_id: str,
        product_images: list[dict[str, str]],
        emit: EmitFn,
        # scene_id -> {mime_type, data}: a scene's own generated hero image.
        # See generate_scene_clip for what a clip does with it — a scene named
        # here opens on what it actually depicts rather than the raw product
        # photo every scene fell back to before this existed.
        scene_images: dict[str, dict[str, str]] | None = None,
        hitl_gates: list[str] | None = None,
        approvals: list[Any] | None = None,
        brand_details: Any = None,
        project_details: Any = None,
        guardrail_set: Any = None,
        scene_ids: list[str] | None = None,
        target_seconds: int | None = None,
        # [{scene_id, clip_id}] the caller already has. Ids, not bytes.
        prior_clips: list[dict[str, str]] | None = None,
        escalation_decisions: list[Any] | None = None,
        scene_revisions: list[Any] | None = None,
        product_details: Any = None,
        audience_segment: str | None = None,
        asset_type: str | None = None,
    ):
        self.concept = concept or {}
        self.storyboard = storyboard or []
        self.copy = copy or {}
        self.brand_kit = brand_kit
        self.instructions = (instructions or "").strip()
        self.aspect_ratio = _coerce_video_aspect(aspect_ratio)
        self.selected_scene_id = str(selected_scene_id or "").strip()
        self.allowed_scene_ids = [str(s).strip() for s in (scene_ids or []) if str(s).strip()]
        self.target_seconds = int(target_seconds) if target_seconds else None
        self.product_images = [p for p in (product_images or []) if p.get("data")]
        self.scene_images = {
            str(sid): img for sid, img in (scene_images or {}).items() if img and img.get("data")
        }
        self.emit = emit
        self.gates = GateSet(hitl_gates)
        self.approvals = Approvals(approvals)
        self.pending_gate: dict[str, Any] | None = None
        self.brand_details = coerce_brand(brand_details)
        self.project_details = coerce_project(project_details)
        self.product_details = coerce_product(product_details)
        self.audience_segment = (audience_segment or "").strip() or None
        self.asset_type = (asset_type or "").strip() or None
        self.escalation_decisions = EscalationDecisions(escalation_decisions)
        self.scene_revisions: dict[str, str] = coerce_scene_revisions(scene_revisions)
        self.overrides: list[dict[str, Any]] = []
        self.guardrails: GuardrailSet = resolve_guardrails(
            guardrail_set, brand=self.brand_details, project=self.project_details,
            product=self.product_details,
        )

        # Product photo frames (start required; end only if a distinct 2nd photo exists)
        self.start_frame: dict[str, str] | None = (
            self.product_images[0] if self.product_images else None
        )
        self.end_frame: dict[str, str] | None = None
        if len(self.product_images) >= 2:
            a, b = self.product_images[0], self.product_images[1]
            if a.get("data") and b.get("data") and a["data"] != b["data"]:
                self.end_frame = b

        # scene_id -> {bytes, mime, motion_prompt, duration_seconds, clip_id?, reused?}
        self.clips: dict[str, dict[str, Any]] = {}
        # Clips the caller already holds, loaded from local disk rather than
        # re-rendered. See clip_store for why they are not sent in the body.
        # A scene a human asked to change is NOT reused: they are looking at the
        # cut they want different, so handing it back is the one answer that
        # cannot be right.
        for ref in prior_clips or []:
            scene = str((ref or {}).get("scene_id") or "").strip()
            clip_id = str((ref or {}).get("clip_id") or "").strip()
            if not scene or not clip_id or scene in self.scene_revisions:
                continue
            stored = clip_store.get(clip_id)
            if stored is None:
                # Swept, or never written. Not an error — it just gets rendered
                # again, which is what happened on every call before this.
                log.info("clip %s for %s is no longer on disk; will re-render",
                         clip_id, scene)
                continue
            self.clips[scene] = {
                "bytes": stored["bytes"],
                "mime": stored["mime"],
                "motion_prompt": stored["motion_prompt"],
                "duration_seconds": stored["duration_seconds"],
                "clip_id": clip_id,
                "reused": True,
            }
        self.finalized = False
        self.final_video_b64: str | None = None
        self.final_mime: str = "video/mp4"

    @property
    def product_images_b64(self) -> list[dict[str, str]]:
        """
        Same photos CampaignRunContext calls product_images_b64 — this context
        calls the attribute product_images because it also derives start_frame
        and end_frame from it, a distinction only video needs. Shared helpers
        that just want "the product photos" — _ensure_guardrails is the one
        that actually crashed on this — should not have to know which context
        they were handed. AttributeError on this exact name, product images
        attached, infer_guardrails at its default of True: that combination is
        every real video call this backend has ever served, and it was never
        exercised by a test that didn't specifically turn inference off.
        """
        return self.product_images

    def brand_block(self, role: str = VIDEO_DIRECTOR) -> str:
        """
        Brand and project context for a prompt, sliced for the role asking.

        Defaults to the video director because that is who runs here; the
        guardrail author passes its own role. Typed details win, a legacy kit
        still renders, and an empty result is a usable sentence.
        """
        return context_block(
            brand=self.brand_details,
            project=self.project_details,
            product=self.product_details,
            role=role,
            legacy_kit=self.brand_kit,
            fallback="No brand context provided. Use a clean modern commercial aesthetic.",
        )

    def selected_scene(self) -> dict[str, Any]:
        for s in self.storyboard:
            if str(s.get("id") or "").strip() == self.selected_scene_id:
                return s
        return {"id": self.selected_scene_id}

    def active_guardrails(self) -> GuardrailSet:
        """Rules that apply to this cut — see CampaignRunContext.active_guardrails."""
        return self.guardrails.scoped_to(
            segment=self.audience_segment, asset_type=self.asset_type
        )

    def scene_ids(self) -> list[str]:
        """
        Every scene this run may cut, in playback order.

        Used to be exactly one. A provider clip is at most 8 seconds, so a single
        clip could only ever be an 8-second film — and a 15-second reel or a
        30-second hero was not expressible, despite finalize_video having always
        known how to stitch several together.
        """
        if self.allowed_scene_ids:
            return list(self.allowed_scene_ids)
        if self.selected_scene_id:
            return [self.selected_scene_id]
        return [str(s.get("id")) for s in self.storyboard if s.get("id")]

    def _frames_for(self, scene_id: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
        """
        The start and end frame for one scene's clip.

        A scene with its own generated image opens on what it actually
        depicts — a storyboard that calls for a clean flat-lay on one cut and
        an on-model lifestyle shot on the next now produces two clips that
        look like those two things, rather than both animating the same
        uploaded product photo with only the text prompt telling them apart.
        A scene named nowhere in scene_images falls back to that product
        photo exactly as every scene did before this existed.

        The end frame follows the same logic one step further: with scene
        images available at all, a cut ends on the NEXT scene's own image, so
        playback flows toward what comes after it rather than an arbitrary
        second product photo with no relation to the story. The last cut has
        no next scene to flow into, so it has no forced end frame — nothing
        else in this codebase treats "no end frame" as invalid; the model
        animates it from the start frame alone. Only when NO scene images were
        supplied anywhere — a video_director with no scene_image_generator
        upstream — does the single film-wide pair this run may have carry
        every cut, unchanged from before this existed.
        """
        start = self.scene_images.get(scene_id) or self.start_frame
        if not self.scene_images:
            return start, self.end_frame
        cuts = self.scene_ids()
        idx = cuts.index(scene_id) if scene_id in cuts else -1
        if 0 <= idx < len(cuts) - 1:
            return start, self.scene_images.get(cuts[idx + 1])
        return start, None

    def clip_refs(self) -> list[dict[str, Any]]:
        """
        What the caller should send back next time, in storyboard order.

        Ids only. The bytes are on disk and a film's worth of them would not
        fit in a request anyway — which is the whole reason the store exists.
        A clip that failed to file is omitted rather than named: an id that
        resolves to nothing would just be re-rendered, and saying so here
        would promise something this run cannot deliver.
        """
        out: list[dict[str, Any]] = []
        for scene_id in self.scene_ids():
            clip = self.clips.get(scene_id)
            if not clip or not clip.get("clip_id"):
                continue
            out.append({
                "scene_id": scene_id,
                "clip_id": clip["clip_id"],
                "duration_seconds": clip.get("duration_seconds") or 0,
                "size_bytes": len(clip.get("bytes") or b""),
                "reused": bool(clip.get("reused")),
            })
        return out

    def cut_plan(self) -> str:
        """
        How to spend the target length, as guidance rather than a schedule.

        The Director picks the cuts; this says what the arithmetic allows, so it
        does not have to work out that 30 seconds is four clips when the longest
        one available is 8.
        """
        longest = max(SUPPORTED_VIDEO_DURATIONS_SECONDS)
        shortest = min(SUPPORTED_VIDEO_DURATIONS_SECONDS)
        allowed = ", ".join(f"{d}s" for d in sorted(SUPPORTED_VIDEO_DURATIONS_SECONDS))
        scenes = self.scene_ids()
        listed = ", ".join(scenes) or "(none)"

        base = (
            f"One clip per scene, stitched in order. Each clip may be {allowed}. "
            f"Scenes to cut, in order: {listed}."
        )
        if not self.target_seconds:
            return base

        reach = len(scenes) * longest
        floor_len = len(scenes) * shortest
        plan = (
            f"{base}\nTarget length: about {self.target_seconds}s. With {len(scenes)} scene(s) "
            f"the film can run {floor_len}-{reach}s, so choose durations that add up close to "
            "the target rather than making every cut the same length."
        )
        if self.target_seconds > reach:
            # Said plainly rather than silently returning a short film: the caller
            # asked for something the storyboard cannot cover.
            plan += (
                f"\nWARNING: {self.target_seconds}s is not reachable from {len(scenes)} "
                f"scene(s) — the longest possible film here is {reach}s. Make the longest "
                "film you can and say so in the summary."
            )
        return plan

    async def generate_scene_clip(self, args: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(args.get("scene_id") or self.selected_scene_id or "").strip()
        motion_prompt = str(args.get("motion_prompt") or "").strip()
        # Chosen per cut now, not fixed. A film is built from clips of differing
        # length, and forcing every one to 8s made pacing impossible as well as
        # capping the whole film at 8 seconds.
        duration = _coerce_duration(args.get("duration_seconds"))

        if not scene_id or not motion_prompt:
            return {"error": "scene_id and motion_prompt are required"}

        allowed = self.scene_ids()
        if allowed and scene_id not in allowed:
            return {
                "error": (
                    f"{scene_id} is not one of this run's scenes. Use one of: "
                    f"{', '.join(allowed)}."
                ),
            }

        # Already rendered on an earlier call and still on disk. Answered before
        # the gate below, because a scene that is not going to be rendered has
        # nothing to approve the spending of — and asking again for a clip the
        # human already approved is how one gate turned into N(N+1)/2 renders.
        held = self.clips.get(scene_id)
        if held is not None and held.get("reused"):
            # Emitted, not just returned. A saving nothing reports looks
            # identical to the bug it fixes: the Steps view showed "clip ready"
            # for a cut that had been paid for twice, and would show nothing at
            # all for one that now costs nothing.
            await self.emit(
                {
                    "type": "clip_reused",
                    "agent": "video_director",
                    "message": (
                        f"Clip for {scene_id} reused from the store "
                        f"({held.get('duration_seconds')}s) — not re-rendered"
                    ),
                    "data": {
                        "sceneId": scene_id,
                        "durationSeconds": held.get("duration_seconds"),
                        "sizeBytes": len(held.get("bytes") or b""),
                        "clipId": held.get("clip_id"),
                    },
                }
            )
            return {
                "ok": True,
                "sceneId": scene_id,
                "reused": True,
                "durationSeconds": held.get("duration_seconds"),
                "sizeBytes": len(held.get("bytes") or b""),
                "message": (
                    f"{scene_id} was already rendered and is being reused, not "
                    f"charged for again. {len(self.clips)}/{len(allowed or [scene_id])} "
                    "scenes done."
                ),
                "remainingScenes": [s for s in self.scene_ids() if s not in self.clips],
            }

        if self.gates.is_gated(GATE_PRE_VIDEO, scene_id) and not self.approvals.approved(
            GATE_PRE_VIDEO, scene_id
        ):
            block = gate_payload(
                gate=GATE_PRE_VIDEO,
                subject_id=scene_id,
                payload={
                    "scene_id": scene_id,
                    "motion_prompt": motion_prompt,
                    "duration_seconds": duration,
                    "aspect_ratio": self.aspect_ratio,
                },
            )
            if self.pending_gate is None:
                self.pending_gate = block
                await self.emit(
                    {
                        "type": "awaiting_approval",
                        "agent": "video_director",
                        "message": f"Approval needed before rendering {scene_id}",
                        "data": block,
                    }
                )
            # Wrapped as terminal so the agent loop stops here, on this turn,
            # instead of being told "do not retry" in a tool result and left
            # to decide for itself what eight turns of budget are for. It does
            # not always decide well: told exactly this, it has both announced
            # it would stop and then called this tool again anyway, burning
            # the loop out to "exceeded max turns" with the gate it needed to
            # report sitting correctly set in self.pending_gate the entire
            # time — a real answer, discarded because nothing told the harness
            # the question was already answered. run_video_director lists this
            # tool as terminal for exactly this return shape; a normal,
            # ungated clip returns a plain dict below and the loop continues
            # exactly as before.
            return terminal(
                {
                    "awaiting_approval": True,
                    "gate": GATE_PRE_VIDEO,
                    "subject_id": scene_id,
                    "message": (
                        f"Approval needed before rendering {scene_id}. Do not retry; "
                        "report that the clip is waiting on a human."
                    ),
                }
            )

        edited = self.approvals.edits_for(GATE_PRE_VIDEO, scene_id) or {}
        if edited.get("motion_prompt"):
            motion_prompt = str(edited["motion_prompt"]).strip()
        start_frame, end_frame = self._frames_for(scene_id)
        if not start_frame or not start_frame.get("data"):
            return {"error": "Product image is required as the video start frame"}

        await self.emit(
            {
                "type": "agent_message",
                "agent": "video_director",
                "message": (
                    f"Generating 8s clip for {scene_id} from "
                    + ("its own generated scene" if scene_id in self.scene_images
                       else "the product photo")
                ),
                "data": {
                    "sceneId": scene_id,
                    "durationSeconds": duration,
                    "motionPrompt": motion_prompt[:400],
                    "hasEndFrame": bool(end_frame),
                },
            }
        )

        try:
            operation = await submit_video_generation(
                VideoGenerationRequest(
                    prompt=motion_prompt,
                    image_base64=start_frame["data"],
                    image_mime_type=start_frame.get("mime_type") or "image/jpeg",
                    end_image_base64=(end_frame or {}).get("data"),
                    end_image_mime_type=(end_frame or {}).get("mime_type") or "image/jpeg",
                    aspect_ratio=self.aspect_ratio,
                    sample_count=1,
                    duration_seconds=duration,
                    resolution="720p",
                    brand_memory=brand_memory_from_kit(self.brand_kit, self.brand_details),
                )
            )
        except Exception as exc:
            log.error("submit_video failed for %s: %s", scene_id, exc, exc_info=True)
            await self.emit(
                {
                    "type": "error",
                    "agent": "video_director",
                    "message": f"Clip submit failed for {scene_id}: {exc}",
                    "data": {"sceneId": scene_id},
                }
            )
            return {"ok": False, "sceneId": scene_id, "error": str(exc)}

        # Poll until done. Transient Gemini outages ("service unavailable") are
        # common mid-run — retry polls, and resubmit the job a couple of times
        # instead of hanging forever on a dead operation.
        max_attempts = min(VIDEO_POLL_MAX_ATTEMPTS, 90)  # ~12 min per clip
        consecutive_poll_errors = 0
        resubmits = 0
        max_resubmits = 2

        async def _resubmit() -> str | None:
            nonlocal resubmits
            if resubmits >= max_resubmits:
                return None
            resubmits += 1
            await self.emit(
                {
                    "type": "agent_message",
                    "agent": "video_director",
                    "message": (
                        f"Gemini unstable for {scene_id} — resubmitting clip "
                        f"(attempt {resubmits}/{max_resubmits})"
                    ),
                    "data": {"sceneId": scene_id, "resubmit": resubmits},
                }
            )
            try:
                return await submit_video_generation(
                    VideoGenerationRequest(
                        prompt=motion_prompt,
                        image_base64=self.start_frame["data"],
                        image_mime_type=self.start_frame.get("mime_type") or "image/jpeg",
                        end_image_base64=(self.end_frame or {}).get("data"),
                        end_image_mime_type=(self.end_frame or {}).get("mime_type")
                        or "image/jpeg",
                        aspect_ratio=self.aspect_ratio,
                        sample_count=1,
                        duration_seconds=duration,
                        resolution="720p",
                        brand_memory=brand_memory_from_kit(self.brand_kit, self.brand_details),
                    )
                )
            except Exception as exc:
                log.error("resubmit_video failed for %s: %s", scene_id, exc, exc_info=True)
                return None

        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)
            try:
                result = await poll_video_operation(operation)
                consecutive_poll_errors = 0
            except Exception as exc:
                consecutive_poll_errors += 1
                msg = str(exc)
                log.warning(
                    "poll_video failed attempt=%d scene=%s consecutive=%d: %s",
                    attempt,
                    scene_id,
                    consecutive_poll_errors,
                    msg,
                )
                if consecutive_poll_errors == 1 or consecutive_poll_errors % 5 == 0:
                    await self.emit(
                        {
                            "type": "agent_message",
                            "agent": "video_director",
                            "message": (
                                f"Waiting on Gemini for {scene_id} "
                                f"(temporary error: {msg[:120]})"
                            ),
                            "data": {
                                "sceneId": scene_id,
                                "attempt": attempt,
                                "pollError": msg[:200],
                            },
                        }
                    )
                # After several unavailable polls, the operation is likely dead — resubmit.
                if consecutive_poll_errors >= 5:
                    new_op = await _resubmit()
                    if new_op:
                        operation = new_op
                        consecutive_poll_errors = 0
                        continue
                    return {
                        "ok": False,
                        "sceneId": scene_id,
                        "error": (
                            f"Gemini stayed unavailable while rendering {scene_id}. "
                            "Skip this scene and finalize with ready clips, or retry later."
                        ),
                        "ready": list(self.clips.keys()),
                        "canFinalizePartial": bool(self.clips),
                    }
                continue

            if not result.done:
                if attempt % 5 == 0:
                    await self.emit(
                        {
                            "type": "agent_message",
                            "agent": "video_director",
                            "message": (
                                f"Still rendering clip for {scene_id} "
                                f"(~{attempt * VIDEO_POLL_INTERVAL_SECONDS}s)"
                            ),
                            "data": {"sceneId": scene_id, "attempt": attempt},
                        }
                    )
                continue

            if result.error:
                # Operation failed server-side — try one resubmit before giving up.
                new_op = await _resubmit()
                if new_op:
                    operation = new_op
                    consecutive_poll_errors = 0
                    continue
                await self.emit(
                    {
                        "type": "error",
                        "agent": "video_director",
                        "message": f"Clip failed for {scene_id}: {result.error}",
                        "data": {"sceneId": scene_id},
                    }
                )
                return {
                    "ok": False,
                    "sceneId": scene_id,
                    "error": result.error,
                    "ready": list(self.clips.keys()),
                    "canFinalizePartial": bool(self.clips),
                }

            data_url = result.data_url
            if not data_url and result.videos:
                data_url = result.videos[0].data_url
            if not data_url:
                return {"ok": False, "sceneId": scene_id, "error": "No video data returned"}

            video_bytes, mime = _decode_data_url(data_url)
            # Filed on local disk before anything else can go wrong with this
            # request. A clip that exists only in this process is a clip the
            # next call has to pay for again.
            clip_id = clip_store.put(
                scene_id=scene_id,
                data=video_bytes,
                mime=mime or result.mime_type or "video/mp4",
                duration_seconds=duration,
                motion_prompt=motion_prompt,
            )
            self.clips[scene_id] = {
                "bytes": video_bytes,
                "mime": mime or result.mime_type or "video/mp4",
                "motion_prompt": motion_prompt,
                "duration_seconds": duration,
                "clip_id": clip_id,
            }
            await self.emit(
                {
                    "type": "clip_ready",
                    "agent": "video_director",
                    "message": f"Clip ready for {scene_id} ({duration}s)",
                    "data": {
                        "sceneId": scene_id,
                        "durationSeconds": duration,
                        "sizeBytes": len(video_bytes),
                        "clipId": clip_id,
                    },
                }
            )
            return {
                "ok": True,
                "sceneId": scene_id,
                "durationSeconds": duration,
                "sizeBytes": len(video_bytes),
                "stored": bool(clip_id),
                "message": f"Clip ready. {len(self.clips)}/{len(self.scene_ids())} scenes done.",
                "remainingScenes": [s for s in self.scene_ids() if s not in self.clips],
            }

        return {
            "ok": False,
            "sceneId": scene_id,
            "error": f"Timed out waiting for clip {scene_id}",
            "ready": list(self.clips.keys()),
            "canFinalizePartial": bool(self.clips),
        }

    async def finalize_video(self, args: dict[str, Any]) -> dict[str, Any]:
        order_raw = args.get("order") or self.scene_ids()
        if not isinstance(order_raw, list) or not order_raw:
            order_raw = list(self.clips.keys())
        if not order_raw and self.clips:
            order_raw = list(self.clips.keys())
        if not isinstance(order_raw, list) or not order_raw:
            return {"error": "order must be a non-empty list of scene ids"}

        order = [str(s).strip() for s in order_raw if str(s).strip()]
        # Drop scenes that never got a clip (partial film after Gemini outages)
        missing = [s for s in order if s not in self.clips]
        order = [s for s in order if s in self.clips]
        for sid in self.scene_ids():
            if sid in self.clips and sid not in order:
                order.append(sid)

        if not order:
            return {
                "error": "No clips ready to stitch. Generate at least one scene clip first.",
                "missing": missing,
                "ready": list(self.clips.keys()),
            }

        if missing:
            await self.emit(
                {
                    "type": "agent_message",
                    "agent": "video_director",
                    "message": (
                        f"Stitching partial film — skipped scenes without clips: "
                        f"{', '.join(missing)}"
                    ),
                    "data": {"missing": missing, "order": order},
                }
            )

        summary = str(
            args.get("summary")
            or f"Campaign film with {len(order)} scenes"
        )

        await self.emit(
            {
                "type": "agent_message",
                "agent": "video_director",
                "message": f"Stitching {len(order)} clips into the campaign film",
                "data": {"order": order},
            }
        )

        tmpdir = tempfile.mkdtemp(prefix="campaign-video-")
        try:
            clip_paths: list[str] = []
            for i, sid in enumerate(order):
                clip = self.clips[sid]
                path = os.path.join(tmpdir, f"clip-{i:02d}-{sid}.mp4")
                with open(path, "wb") as f:
                    f.write(clip["bytes"])
                clip_paths.append(path)

            if len(clip_paths) == 1:
                out_path = clip_paths[0]
                with open(out_path, "rb") as f:
                    final_bytes = f.read()
            else:
                out_path = os.path.join(tmpdir, "campaign-final.mp4")
                await _stitch_clips_ffmpeg(clip_paths, out_path)
                with open(out_path, "rb") as f:
                    final_bytes = f.read()

            self.final_video_b64 = base64.b64encode(final_bytes).decode("ascii")
            self.final_mime = "video/mp4"
            self.finalized = True

            clip_meta = [
                {
                    "sceneId": sid,
                    "durationSeconds": self.clips[sid]["duration_seconds"],
                    "motionPrompt": self.clips[sid]["motion_prompt"][:300],
                }
                for sid in order
            ]

            payload = {
                "summary": summary,
                "order": order,
                "clips": clip_meta,
                "mimeType": self.final_mime,
                "videoBase64": self.final_video_b64,
                "sizeBytes": len(final_bytes),
            }

            await self.emit(
                {
                    "type": "campaign_video_complete",
                    "agent": "video_director",
                    "message": summary,
                    "data": payload,
                }
            )
            return terminal(
                {
                    "summary": summary,
                    "order": order,
                    "clips": clip_meta,
                    "sizeBytes": len(final_bytes),
                }
            )
        except Exception as exc:
            log.error("finalize_video failed: %s", exc, exc_info=True)
            await self.emit(
                {
                    "type": "error",
                    "agent": "video_director",
                    "message": f"Failed to stitch campaign video: {exc}",
                }
            )
            return {"error": str(exc)}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
