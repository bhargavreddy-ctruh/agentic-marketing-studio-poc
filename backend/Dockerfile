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

RUN mkdir -p var/assets

# Pre-fetch the models that are otherwise downloaded lazily on first use, so a fresh deploy's
# first real request isn't slowed down by a cold download. HF_HUB_DISABLE_XET=1 is the same fix
# used at runtime in docker-compose.yml (the newer XET transfer backend hangs in a container
# network) — needed here too, or this build step itself hangs the same way.
ENV HF_HUB_DISABLE_XET=1
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
from faster_whisper import WhisperModel; \
WhisperModel('small', device='cpu', compute_type='int8'); \
import laya; \
laya.load('convaiinnovations/laya')"

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
