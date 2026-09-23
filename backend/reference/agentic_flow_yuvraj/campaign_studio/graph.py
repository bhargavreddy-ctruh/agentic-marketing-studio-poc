"""
Supervisor-driven Campaign Studio orchestrator.

Starts the Creative Director agent (Claude + tools). Events are pushed to an
asyncio.Queue as agents work, and yielded as an SSE-friendly async iterator.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from .agents.crew import run_creative_director, run_video_director, run_video_director_direct
from .config import DEFAULT_ASPECT_RATIO, DEFAULT_ASSET_COUNT, MAX_ASSET_COUNT
from .llm_provider import CampaignLLMError, require_llm_key
from .logger import get_logger
from .state import CampaignEvent
from .tools import CampaignRunContext
from .video_tools import VideoRunContext

log = get_logger(__name__)


def initial_params(
    idea: str,
    product_image_urls: list[str],
    product_images_b64: list[dict[str, str]],
    brand_kit: dict[str, Any] | None,
    asset_count: int | None,
    aspect_ratio: str | None,
) -> dict[str, Any]:
    count = max(1, min(int(asset_count or DEFAULT_ASSET_COUNT), MAX_ASSET_COUNT))
    return {
        "idea": idea,
        "product_image_urls": product_image_urls or [],
        "product_images_b64": product_images_b64 or [],
        "brand_kit": brand_kit,
        "asset_count": count,
        "aspect_ratio": aspect_ratio or DEFAULT_ASPECT_RATIO,
    }


async def run_campaign_events(
    idea: str,
    product_image_urls: list[str],
    product_images_b64: list[dict[str, str]],
    brand_kit: dict[str, Any] | None = None,
    asset_count: int | None = None,
    aspect_ratio: str | None = None,
    brand_details: Any = None,
    project_details: Any = None,
) -> AsyncIterator[CampaignEvent]:
    """
    Run the Creative Director supervisor agent and yield CampaignEvents as they happen.
    Signature preserved for routes.py / studio-backend worker compatibility.
    """
    try:
        require_llm_key()
    except CampaignLLMError as exc:
        yield {
            "type": "error",
            "agent": "system",
            "message": str(exc),
        }
        return

    params = initial_params(
        idea, product_image_urls, product_images_b64, brand_kit, asset_count, aspect_ratio
    )

    queue: asyncio.Queue[CampaignEvent | None] = asyncio.Queue()

    async def emit(event: CampaignEvent) -> None:
        await queue.put(event)

    ctx = CampaignRunContext(
        idea=params["idea"],
        product_images_b64=params["product_images_b64"],
        product_image_urls=params["product_image_urls"],
        brand_kit=params["brand_kit"],
        asset_count=params["asset_count"],
        aspect_ratio=params["aspect_ratio"],
        emit=emit,
        brand_details=brand_details,
        project_details=project_details,
    )

    yield {
        "type": "agent_started",
        "agent": "creative_director",
        "message": "Campaign crew assembled — Creative Director taking the lead",
    }

    async def _run() -> None:
        try:
            await run_creative_director(ctx)
            if not ctx.finalized:
                # Escalations outrank the synthesized-completion fallback: the supervisor
                # may have stopped precisely because it could not finalize, and quietly
                # finalizing here would restore the behaviour we just removed.
                pending = ctx.open_escalations()
                if pending:
                    scene_ids = ", ".join(str(e.get("sceneId")) for e in pending)
                    await emit(
                        {
                            "type": "awaiting_human",
                            "agent": "creative_director",
                            "message": (
                                f"Run stopped for human review — {len(pending)} scene(s) "
                                f"rejected past the retry limit: {scene_ids}"
                            ),
                            "data": {"escalations": pending},
                        }
                    )
                    return
                # Supervisor returned without finalize — synthesize completion if possible
                assets = [a for a in ctx.assets.values() if a.get("imageBase64")]
                failed_qa = [
                    sid
                    for sid, asset in ctx.assets.items()
                    if asset.get("imageBase64")
                    and isinstance(asset.get("qa"), dict)
                    and not asset["qa"].get("passed")
                ]
                if ctx.needs_human_review or failed_qa:
                    ctx.needs_human_review = True
                    await emit(
                        {
                            "type": "needs_human_review",
                            "agent": "creative_director",
                            "message": "Campaign paused for human review — QA did not pass after max retries",
                            "data": {
                                "needsHumanReview": True,
                                "failedScenes": failed_qa,
                                "concept": ctx.concept,
                                "storyboard": ctx.storyboard,
                                "copy": ctx.copy,
                                "assetCount": len(assets),
                            },
                        }
                    )
                elif assets and ctx.concept:
                    await ctx.finalize_campaign(
                        {"summary": f"Campaign assembled with {len(assets)} assets"}
                    )
                else:
                    await emit(
                        {
                            "type": "error",
                            "agent": "creative_director",
                            "message": "Creative Director ended without finalizing the campaign",
                        }
                    )
        except Exception as exc:
            log.error("campaign supervisor failed: %s", exc, exc_info=True)
            await emit(
                {
                    "type": "error",
                    "agent": "creative_director",
                    "message": str(exc),
                }
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(_run())

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


async def run_video_events(
    *,
    concept: dict[str, Any],
    storyboard: list[dict[str, Any]],
    copy: dict[str, Any],
    brand_kit: dict[str, Any] | None,
    instructions: str,
    aspect_ratio: str | None,
    selected_scene_id: str,
    product_images: list[dict[str, str]],
) -> AsyncIterator[CampaignEvent]:
    """
    Run the Video Director for one selected scene (8s) using product photo frames.
    """
    try:
        require_llm_key()
    except CampaignLLMError as exc:
        yield {"type": "error", "agent": "system", "message": str(exc)}
        return

    if not selected_scene_id:
        yield {
            "type": "error",
            "agent": "system",
            "message": "selected_scene_id is required",
        }
        return
    if not product_images:
        yield {
            "type": "error",
            "agent": "system",
            "message": "At least one product image is required as the video frame",
        }
        return

    queue: asyncio.Queue[CampaignEvent | None] = asyncio.Queue()

    async def emit(event: CampaignEvent) -> None:
        await queue.put(event)

    ctx = VideoRunContext(
        concept=concept or {},
        storyboard=storyboard or [],
        copy=copy or {},
        brand_kit=brand_kit,
        instructions=instructions or "",
        aspect_ratio=aspect_ratio or "16:9",
        selected_scene_id=selected_scene_id,
        product_images=product_images,
        emit=emit,
    )

    yield {
        "type": "agent_started",
        "agent": "video_director",
        "message": f"Video Director starting — 8s clip for {selected_scene_id}",
        "data": {"sceneId": selected_scene_id},
    }

    async def _run() -> None:
        try:
            try:
                await run_video_director(ctx)
            except CampaignLLMError as exc:
                log.warning("video_director LLM failed (%s); using direct Gemini clip path", exc)
                await run_video_director_direct(ctx)
            if not ctx.finalized:
                ready = [s for s in ctx.scene_ids() if s in ctx.clips]
                if ready:
                    await ctx.finalize_video(
                        {
                            "order": ready,
                            "summary": f"8s campaign clip for {ready[0]}",
                        }
                    )
                else:
                    await emit(
                        {
                            "type": "error",
                            "agent": "video_director",
                            "message": "Video Director ended without generating a clip",
                        }
                    )
        except Exception as exc:
            log.error("video director failed: %s", exc, exc_info=True)
            await emit(
                {
                    "type": "error",
                    "agent": "video_director",
                    "message": str(exc),
                }
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(_run())

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
