# Running with Docker (local)

Access the app at **http://localhost:3000** after starting.

## First-time setup

### 1. Copy and fill in API keys
```bash
cp backend/.env.example backend/.env
```
Edit `backend/.env` — add at minimum:
```
OPENROUTER_API_KEY=...
GROQ_API_KEY=...
REPLICATE_API_TOKEN=...
```
Do NOT set `LOCAL_LLM_BASE_URL`, `FRONTEND_ORIGINS`, or `DATABASE_URL` — `docker-compose.yml` sets
those for you (they need container-network values, not your host machine's).

### 2. (Optional) Set compose-level URLs and the Ollama model
```bash
cp .env.example .env
```
Defaults are fine for a same-machine run (`localhost`). Only edit `OLLAMA_MODEL` if you're using
something other than `llama3.1:8b` — it must match `backend/.env`'s own
`LOCAL_LLM_MODEL_TIER_1`, or TIER_1 local-preferring specialists will silently fall through to
Groq instead of using your local model.

### 3. Build and start
```bash
docker compose up --build -d
```
First build: 5–10 min — this also pre-downloads the embedding/whisper/Laya models into the image
and, on first start only, the Ollama model (both fully automatic now, no manual pull step). After
that: `docker compose up -d` (seconds).

### 4. Open
http://localhost:3000

## Day-to-day

| Command | What it does |
|---|---|
| `docker compose up -d` | Start all services |
| `docker compose down` | Stop all services |
| `docker compose logs -f backend` | Tail backend logs |
| `docker compose up --build -d` | Rebuild after code changes |

## Data persistence

| Data | Where |
|---|---|
| SQLite DB | `backend/poc.db` on your Mac (bind-mount) |
| Generated images/videos/audio | `backend/var/assets/` on your Mac (bind-mount) |
| Ollama model weights | `ollama_data` Docker named volume |

`docker compose down` never deletes data. Only `docker compose down -v` removes the Ollama weights
volume.
