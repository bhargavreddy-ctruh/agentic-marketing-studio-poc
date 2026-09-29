# Running with Docker (local)

Access the app at **http://localhost:3000** after starting.

## First-time setup

### 1. Copy the ONE env file and fill in `DATABASE_URL`
```bash
cp .env.example .env
```
`DATABASE_URL` is **required** — `docker compose up` refuses to start at all without it (a real
Supabase Postgres connection string; see `.env.example`'s own comment for the exact format). No
silent SQLite fallback (2026-09-29, after that fallback masked a missing `.env` for hours in a
real incident) — this is the only env file `docker-compose.yml` reads, for everything.

Real API keys (Groq, Replicate, HuggingFace, Cloudflare, Cloudinary, LangSmith) are **not** set in
any env file — they're DB-managed via Supabase's `app_settings` table (`.env.example`'s bottom
section has the exact key list + an INSERT template), applied live with no restart needed.

`FIRECRAWL_API_KEY` (also DB-managed) is optional — only needed for the Product/Brand crawler's
Firecrawl fallback; it still works via Playwright alone without it.

### 2. Build and start
```bash
docker compose up --build -d
```
First build: 5–10 min — this also pre-downloads the embedding/whisper/Laya models into the image
and, on first start only, the crawler's Ollama model (both fully automatic now, no manual pull
step). After that: `docker compose up -d` (seconds).

### 3. Open
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
| Sessions, canvas elements, brand/product DNA | Supabase Postgres (`DATABASE_URL` in `.env`) — not on this machine at all |
| Generated images/videos/audio | Cloudinary (if `cloudinary_url` is set in `app_settings`), else `backend/var/assets/` on your Mac (bind-mount) |
| Crawler's Ollama model weights | `ollama_data` Docker named volume |

`docker compose down` never deletes data. Only `docker compose down -v` removes the Ollama weights
volume — Supabase/Cloudinary data is never affected by anything run on this machine.
