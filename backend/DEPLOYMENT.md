# Backend Deployment (AWS EC2 + GitHub Actions)

How the backend gets deployed to AWS, and how the automatic CI/CD pipeline works. This is the
doc to hand to whoever provisions the EC2 instance — read this before touching AWS.

## How it works, in one sentence

Every push to `main` that touches `backend/` or `docker-compose.yml` runs tests, then — if they
pass — SSHes into a pre-provisioned EC2 instance, pulls the new code, and restarts the backend
container with `docker compose`. Nothing else needs to change by hand after the one-time setup
below.

This intentionally reuses [docker-compose.yml](../docker-compose.yml) as-is — the same file used
for local dev — rather than introducing a separate AWS-only deployment format (ECS task
definitions, Kubernetes manifests, etc.). One fewer thing to keep in sync, and cheaper to get
right for a small team. If this project later needs autoscaling or zero-downtime deploys, that's
a real reason to move to ECS Fargate — a genuine tradeoff, not something to introduce ahead of
actually needing it.

**This deploys the backend only.** The frontend is a separate concern — see the note at the
bottom.

## One-time setup (devops does this once, per environment)

### 1. Provision the EC2 instance

- **Size**: `t3.small`/`t3.medium` (1-2 vCPU / 2-4 GB RAM) is enough — local Ollama was removed
  (2026-09-28), so nothing on this instance needs to load a full LLM into RAM anymore; all
  reasoning goes to Groq/Replicate. Go larger if you expect concurrent generation requests (the
  heavy Python ML deps — torch, sentence-transformers, faster-whisper — still have their own
  footprint even without Ollama).
- **AMI**: Amazon Linux 2023 or Ubuntu 22.04+, either works.
- **Storage**: 15 GB+ EBS volume. Docker images and generated assets (`backend/var/`, if not
  using Cloudinary) live on this disk — no Ollama model weights to account for anymore.
- **Security group**: open port 22 (SSH, ideally restricted to a known IP range or a bastion —
  not `0.0.0.0/0`) and port 8000 (the backend API).
- **Elastic IP**: attach one, so the instance's address doesn't change on stop/restart — CI's
  `EC2_HOST` secret (below) assumes a stable address.

### 2. Install Docker on the instance

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# log out and back in for the group change to apply
```

Confirm both work: `docker --version` and `docker compose version` (Docker Compose v2 ships as a
plugin with the script above — no separate install needed).

### 3. Clone the repo onto the instance

CI deploys by resetting this exact path to `origin/main` — it must be at this path:

```bash
git clone https://github.com/<org>/<repo>.git ~/agentic-marketing-studio
cd ~/agentic-marketing-studio/poc
```

(Use a GitHub deploy key or a fine-grained PAT with read-only access if the repo is private —
the EC2 instance only ever needs to pull, never push.)

### 4. Create the real env files (never committed — see `.gitignore`)

```bash
cp backend/.env.example backend/.env
cp .env.example .env
```

Edit `backend/.env` — fill in the real API keys (`GROQ_API_KEY`, `OPENROUTER_API_KEY`,
`REPLICATE_API_TOKEN`, etc.). Leave `LOCAL_LLM_BASE_URL`, `FRONTEND_ORIGINS`, and `DATABASE_URL`
unset here — `docker-compose.yml` sets those itself for the container network.

Edit `.env` (compose-level, at the `poc/` root) — set:
```
PUBLIC_FRONTEND_URL=https://your-actual-frontend-domain.com
PUBLIC_BACKEND_URL=https://your-actual-backend-domain.com
OLLAMA_MODEL=llama3.1:8b
```
`PUBLIC_FRONTEND_URL` must match wherever the frontend is actually served from, or the backend
will reject its requests via CORS.

### 5. First manual start (confirms everything works before wiring up CI)

```bash
docker compose up -d --build backend
docker compose logs -f backend   # watch it come up; Ctrl-C to stop tailing
curl http://localhost:8000/health
```

### 6. Add GitHub Actions secrets

In the repo's GitHub settings → **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `EC2_HOST` | The instance's Elastic IP or DNS name |
| `EC2_USER` | The SSH user (e.g. `ubuntu` or `ec2-user`, depending on the AMI) |
| `EC2_SSH_KEY` | The **private** half of an SSH keypair whose public half is in the instance's `~/.ssh/authorized_keys`. Generate a dedicated deploy keypair for this — don't reuse anyone's personal key. |
| `EC2_SSH_PORT` | Optional — only needed if SSH runs on a non-default port. |

That's the entire setup. From this point on, deploys are automatic.

## What happens on every push to `main`

See [.github/workflows/backend-ci-cd.yml](../.github/workflows/backend-ci-cd.yml):

1. **Test job**: installs the backend, runs `ruff check` and `pytest`. A pull request into `main`
   runs this same job (as a required check) without deploying — so a broken PR never reaches
   the deploy step at all.
2. **Deploy job** (only on a push to `main`, only after tests pass): SSHes in, `git fetch` +
   `git reset --hard origin/main` (the deploy path is dedicated to this — never make manual edits
   there, they'll be silently discarded on the next deploy), `docker compose up -d --build
   backend`, then prunes old images so the disk doesn't fill up over time.
3. **Health check**: polls `http://localhost:8000/health` for up to a minute; fails the workflow
   loudly if the backend doesn't come back up, instead of silently leaving a broken deploy running.

## Manual deploy / rollback (if you ever need to bypass CI)

```bash
ssh <user>@<ec2-host>
cd ~/agentic-marketing-studio/poc
git fetch origin
git reset --hard <commit-sha>     # roll back to any specific commit
docker compose up -d --build backend ollama
```

## Day-to-day operations on the instance

| Task | Command |
|---|---|
| Tail backend logs | `docker compose logs -f backend` |
| Tail Ollama logs | `docker compose logs -f ollama` |
| Check container health | `docker compose ps` |
| Restart without a rebuild | `docker compose restart backend` |
| Free disk space (old images) | `docker image prune -f` |

Data survives restarts and redeploys: the SQLite DB (`backend/poc.db`) and generated assets
(`backend/var/`) are bind-mounted from the instance's own disk; the Ollama model weights live in
the named `ollama_data` volume. Back up `backend/poc.db` and `backend/var/` periodically —
nothing here does that automatically.

All required models are downloaded automatically — no manual step, ever:
- The embedding, Whisper, and Laya models are pre-baked into the backend image at **build** time
  (see `backend/Dockerfile`), so the first real request after a deploy isn't slowed down by a
  cold download.
- The `hf_cache` named volume is seeded from that pre-baked image content the first time it's
  created. If a volume with that name already exists from a deployment made before this was
  added, it stays empty of the pre-baked models until first use (falls back to the old lazy
  download) — run `docker volume rm poc_hf_cache` (after stopping the stack) to force it to be
  reseeded from the image on the next `up`.
- The Ollama LLM model auto-pulls on every container start (a no-op, fast, if already present).

## About the frontend

This pipeline deploys the **backend only**. The frontend used in this project
(`agentic_flow_langgraph/frontend`, a sibling directory) is not currently in this Git repository
and has its own separate deployment question — worth raising with whoever owns that piece before
assuming it's covered by this same setup.
