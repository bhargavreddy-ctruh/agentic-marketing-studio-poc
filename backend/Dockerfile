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

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
