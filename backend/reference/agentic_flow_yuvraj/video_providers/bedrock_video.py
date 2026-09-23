"""
bedrock_video.py -- AWS Bedrock Nova Reel video generation provider.

Uses Bedrock REST API (StartAsyncInvoke + GetAsyncInvoke) or boto3 when
AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are set. Fetches generated video from S3.
Requires S3_BUCKET or S3_OUTPUT_BUCKET in [AWS] config.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
from urllib.parse import quote

import httpx

from .types import VideoGenRequest, VideoSubmitResult, VideoPollResult, VideoItem

log = logging.getLogger(__name__)

_TIMEOUT = 120.0

# Aspect ratio to Nova Reel dimension mapping
_ASPECT_TO_DIM = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "1:1": "720x720",
}

# FIX: Nova Reel resolution -> dimension mapping
# Nova Reel does not have a standalone "resolution" parameter like Veo.
# Resolution is controlled via the "dimension" field in videoGenerationConfig.
_RESOLUTION_TO_DIM = {
    "720p":  {"16:9": "1280x720",  "9:16": "720x1280",  "1:1": "720x720"},
    "1080p": {"16:9": "1920x1080", "9:16": "1080x1920", "1:1": "1080x1080"},
    "4k":    {"16:9": "3840x2160", "9:16": "2160x3840", "1:1": "2160x2160"},
}


def _get_dimension(aspect_ratio: str, resolution: str = "720p") -> str:
    """Return Nova Reel dimension string for the given resolution and aspect ratio."""
    res_map = _RESOLUTION_TO_DIM.get(resolution, _RESOLUTION_TO_DIM["720p"])
    return res_map.get(aspect_ratio, res_map["16:9"])


def _has_access_keys(access_key: str, secret_key: str) -> bool:
    return bool(access_key and secret_key)


async def submit_bedrock_video(
    request: VideoGenRequest,
    model: str,
    region: str,
    api_key: str,
    s3_bucket: str,
    *,
    access_key: str = "",
    secret_key: str = "",
    s3_prefix: str = "",
) -> VideoSubmitResult | None:
    """
    Submit a Nova Reel video generation job via Bedrock StartAsyncInvoke.
    Returns VideoSubmitResult with operation_id (invocationArn) for polling.

    Uses boto3 when access_key/secret_key are set; otherwise uses REST API with api_key.
    """
    use_boto3 = _has_access_keys(access_key, secret_key)
    if not use_boto3 and not api_key:
        log.warning("Bedrock video: API_KEY or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY not configured")
        return None
    if not s3_bucket:
        log.warning("Bedrock video: S3_BUCKET or S3_OUTPUT_BUCKET not configured")
        return None

    bucket = s3_bucket.rstrip("/")
    s3_uri = f"s3://{bucket}/{s3_prefix}" if s3_prefix else f"s3://{bucket}"
    effective_prompt = request.prompt.strip()
    if request.brand_memory and request.brand_memory.strip():
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"Brand context: {request.brand_memory.strip()}\n"
            "Apply the brand identity to the visual style and scene composition."
        )

    # FIX: use resolution-aware dimension instead of aspect_ratio-only lookup
    resolution = getattr(request, "resolution", "720p") or "720p"
    dimension = _get_dimension(request.aspect_ratio, resolution)
    log.info("Bedrock video: resolution=%s aspect_ratio=%s -> dimension=%s", resolution, request.aspect_ratio, dimension)

    seed = random.randint(0, 2147483646)
    model_input = {
        "taskType": "TEXT_VIDEO",
        "textToVideoParams": {"text": effective_prompt},
        "videoGenerationConfig": {
            "fps": 24,
            "durationSeconds": getattr(request, "duration_seconds", 8),
            "dimension": dimension,
            "seed": seed,
        },
    }

    # Nova Reel supports image-to-video - add start frame if provided
    if request.image_base64:
        model_input["imageToVideoParams"] = {
            "images": [
                {
                    "format": "png" if "png" in request.image_mime_type.lower() else "jpeg",
                    "source": {"bytes": request.image_base64},
                }
            ]
        }

    payload = {
        "modelId": model,
        "modelInput": model_input,
        "outputDataConfig": {"s3OutputDataConfig": {"s3Uri": s3_uri}},
    }

    if use_boto3:
        def _start_async() -> dict:
            import boto3
            client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            return client.start_async_invoke(
                modelId=model,
                modelInput=model_input,
                outputDataConfig={"s3OutputDataConfig": {"s3Uri": s3_uri}},
            )

        try:
            data = await asyncio.to_thread(_start_async)
        except Exception as exc:
            log.warning("Bedrock video submit (boto3): %s", exc)
            raise RuntimeError(f"Bedrock video submit failed: {exc}") from exc
    else:
        url = f"https://bedrock-runtime.{region}.amazonaws.com/async-invoke"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            log.warning("Bedrock video submit: %s", exc)
            raise

        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("message", body.get("error", resp.text[:200]))
            except Exception:
                msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            log.warning("Bedrock video submit: %s", msg)
            raise RuntimeError(f"Bedrock video submit failed: {msg}")

        data = resp.json()

    invocation_arn = data.get("invocationArn")
    if not invocation_arn:
        raise RuntimeError("Bedrock StartAsyncInvoke did not return invocationArn")

    log.info("Bedrock video: submit accepted invocationArn=%s model=%s", invocation_arn[:60], model)
    return VideoSubmitResult(
        operation_id=invocation_arn,
        provider_used="aws",
        model_used=model,
    )


async def poll_bedrock_video(
    operation_id: str,
    region: str,
    api_key: str,
    *,
    access_key: str = "",
    secret_key: str = "",
) -> VideoPollResult:
    """
    Poll a Bedrock async invocation and fetch the video from S3 when complete.
    Uses boto3 when access_key/secret_key are set; otherwise REST API with api_key.
    """
    use_boto3 = _has_access_keys(access_key, secret_key)

    if use_boto3:
        def _get_async() -> dict:
            import boto3
            client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            return client.get_async_invoke(invocationArn=operation_id)

        try:
            data = await asyncio.to_thread(_get_async)
        except Exception as exc:
            log.warning("Bedrock video poll (boto3): %s", exc)
            raise RuntimeError(f"Bedrock GetAsyncInvoke failed: {exc}") from exc
    else:
        encoded_arn = quote(operation_id, safe="")
        url = f"https://bedrock-runtime.{region}.amazonaws.com/async-invoke/{encoded_arn}"
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            log.warning("Bedrock video poll: %s", exc)
            raise

        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("message", body.get("error", resp.text[:200]))
            except Exception:
                msg = f"HTTP {resp.status_code}"
            raise RuntimeError(f"Bedrock GetAsyncInvoke failed: {msg}")

        data = resp.json()
    status = data.get("status", "")

    if status == "InProgress":
        return VideoPollResult(done=False)

    if status == "Failed":
        failure_msg = data.get("failureMessage", "Video generation failed")
        log.warning("Bedrock video: invocation failed: %s", failure_msg)
        return VideoPollResult(done=True, error=failure_msg)

    if status != "Completed":
        return VideoPollResult(done=True, error=f"Unexpected status: {status}")

    # Completed - fetch video from S3
    output_config = data.get("outputDataConfig", {})
    s3_config = output_config.get("s3OutputDataConfig", {})
    s3_uri = s3_config.get("s3Uri", "")
    if not s3_uri:
        return VideoPollResult(done=True, error="Completed but no S3 output URI in response")

    # s3_uri is like s3://bucket or s3://bucket/prefix; video is at {uri}/output.mp4
    video_s3_key = s3_uri.replace("s3://", "").rstrip("/")
    if not video_s3_key:
        return VideoPollResult(done=True, error="Invalid S3 URI")
    parts = video_s3_key.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    object_key = f"{prefix}/output.mp4".lstrip("/") if prefix else "output.mp4"

    # Fetch from S3 using boto3 (requires AWS credentials)
    try:
        import boto3
    except ImportError:
        log.warning("Bedrock video: boto3 not installed, cannot fetch S3 output")
        return VideoPollResult(
            done=True,
            error="Video ready in S3 but boto3 not installed to fetch. Install boto3 and configure AWS credentials.",
        )

    def _fetch_s3() -> bytes:
        kwargs = {"region_name": region}
        if _has_access_keys(access_key, secret_key):
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        s3 = boto3.client("s3", **kwargs)
        response = s3.get_object(Bucket=bucket, Key=object_key)
        return response["Body"].read()

    try:
        video_bytes = await asyncio.to_thread(_fetch_s3)
    except Exception as exc:
        log.warning("Bedrock video: S3 fetch failed: %s", exc)
        return VideoPollResult(
            done=True,
            error=f"Video generated but S3 fetch failed: {exc}. Check AWS credentials and S3 permissions.",
        )

    b64 = base64.b64encode(video_bytes).decode("utf-8")
    video_item = VideoItem(data_url=f"data:video/mp4;base64,{b64}", mime_type="video/mp4")
    log.info("Bedrock video: poll complete fetched %d bytes from S3", len(video_bytes))
    return VideoPollResult(
        done=True,
        videos=[video_item],
        data_url=video_item.data_url,
        mime_type=video_item.mime_type,
    )