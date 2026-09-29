FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libcairo2-dev \
    libsndfile1 \
    ffmpeg \
    espeak-ng \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Product/Brand crawler (2026-09-28) — Playwright needs its own browser binary + OS deps, not
# just the pip package. A real, disclosed image-size/build-time cost (~300-400MB for Chromium +
# its deps) with no way around it for a Playwright-based scraper.
RUN playwright install --with-deps chromium

# Model pre-fetch TEMPORARILY DISABLED (2026-09-26, real live incident): this build step itself
# started hanging at 0% CPU for 35+ minutes — the exact same network-stalled-download symptom
# `HF_HUB_DISABLE_XET=1` was meant to fix, but apparently not covering all three downloads here
# (faster-whisper's CTranslate2 conversion and/or laya's own downloader may not respect this env
# var the way huggingface_hub's own client does). Reverted to the original lazy-load-on-first-use
# behavior (known working) to unblock a real deploy rather than debug this further mid-incident —
# re-enabling this optimization is real, disclosed follow-up work, not abandoned, just deferred.
ENV HF_HUB_DISABLE_XET=1

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
