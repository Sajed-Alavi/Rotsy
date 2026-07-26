# Installation Guide (0 → 100)

Three ways to run this project:
1. **Docker Compose** (recommended) — one command brings up the whole stack.
2. **Local dev** — run backend and frontend directly for hacking.
3. **Build the images only** — then `docker compose up -d` (no `--build`).

---

## Prerequisites

- A running **Sonatype Nexus Repository Manager** instance (3.x). Either:
  - on the same host (Docker `network_mode: host`), or
  - reachable at a hostname/IP.
- **Docker Engine** ≥ 20.10 and **Docker Compose v2** (`docker compose ...`).
- ~3 GB free for images + Postgres + Redis + scanner DBs.

### If Docker Hub is blocked in your region

Add a registry mirror so image pulls + build base-image fetches go through it:

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://docker.arvancloud.ir"]
}
EOF
sudo systemctl restart docker
```

(Substitute your preferred mirror.) This is required for the build step that
pulls `python:3.11-slim`, `node:20-alpine`, `nginx:alpine`, `postgres:16-alpine`,
and `redis:7-alpine`.

---

## Method 1 — Docker Compose (recommended)

### Step 1. Configure environment

```bash
cd nexus-project
cp .env.example .env
```

Edit `.env`. **Required edits**:

```ini
# Point at your Nexus (use host.docker.internal if Nexus is on the host).
NEXUS_URL=http://host.docker.internal:8081
NEXUS_USERNAME=admin
NEXUS_PASSWORD=<your-nexus-admin-password>
NEXUS_VERIFY_SSL=false   # true in production behind TLS

# A strong random secret. Generate with:  openssl rand -hex 32
JWT_SECRET=<paste output here>

# First admin user of the wrapper (NOT your Nexus user — this is for the UI).
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<pick a strong password>
BOOTSTRAP_ADMIN_EMAIL=admin@example.com

# Postgres credentials (used by the postgres service AND the backend).
POSTGRES_DB=nexus
POSTGRES_USER=nexus
POSTGRES_PASSWORD=<pick a password>
```

Leave everything else at the example defaults unless you know what you're doing.

### Step 2. Build the images (one time)

```bash
docker compose build
```

This takes a few minutes:
- Backend: installs Python deps, **installs Trivy + Grype binaries**, copies
  the app + Alembic migrations.
- Frontend: `npm install` + `vite build`, then packages the bundle into nginx.

If you see TLS handshake timeouts on base image pulls, your Docker Hub mirror
isn't set (see Prerequisites).

### Step 3. Start the stack

```bash
docker compose up -d
```

The first start runs Alembic migrations + seeds (permissions, roles, bootstrap
admin). Tail the backend to confirm:

```bash
docker compose logs -f backend
# look for: "[entrypoint] starting uvicorn..." + "Application startup complete."
```

### Step 4. Use it

- **UI**: http://localhost:8080 — log in with `BOOTSTRAP_ADMIN_USERNAME` /
  `BOOTSTRAP_ADMIN_PASSWORD`.
- **API docs** (Swagger): http://localhost:8000/docs
- **API docs** (ReDoc): http://localhost:8000/redoc

### Step 5. First-time scanner DB warm-up

Trivy/Grype need their vulnerability databases (~hundreds of MB). This is
**automatic** on startup, but to trigger it manually now:

- UI: **Vulnerability Scan → "Refresh vuln DBs"** button.
- Or wait — the periodic loop handles it.

The first scan of any image will be slow while the DB downloads; later scans
are fast.

---

## Method 2 — Local development

Useful when you're modifying code and want hot-reload.

### Backend

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install Trivy + Grype on the host (one-time):
#   Trivy:  https://aquasecurity.github.io/trivy/latest/getting-started/installation/
#   Grype:  https://github.com/anchore/grype#installation

# Export the variables from your .env (or use direnv):
set -a; source ../.env; set +a

# Run Alembic + seed manually the first time:
alembic upgrade head
python -c "import asyncio; \
  from app.config import get_settings; \
  from app.db.seed import run_seed; \
  from app.db.session import get_session_factory; \
  asyncio.run((lambda: run_seed(get_session_factory()(), get_settings()))())"

# Start the API with reload:
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 — proxies /api → :8000
```

You still need Postgres + Redis running somewhere; point `DATABASE_URL` and
`REDIS_URL` at them (e.g. `docker compose up -d postgres redis` only).

---

## Method 3 — Pre-built images, no rebuild

After you've done `docker compose build` once, subsequent starts don't need
`--build`:

```bash
docker compose up -d
```

To force a rebuild after code changes:

```bash
docker compose build backend      # or 'frontend', or both
docker compose up -d
```

---

## Common operations

| Task | Command |
|---|---|
| View logs | `docker compose logs -f backend` (or `frontend`, `postgres`, ...) |
| Restart one service | `docker compose restart backend` |
| Rebuild after code change | `docker compose up -d --build backend` |
| Open a shell in a container | `docker compose exec backend bash` |
| Reset the DB (DESTRUCTIVE) | `docker compose down -v` (drops volumes) |
| Force a metric snapshot now | UI: Metrics → "Collect now" |
| Run retention now | UI: Retention → policy row → "run" |
| Trigger backup | UI: System → "Trigger backup task" |

---

## Troubleshooting

### Backend container restart-loops with a Python traceback
Usually a config error (missing env var) or a migration failure. Read the full
traceback: `docker compose logs --tail 80 backend`. Common fixes:
- Missing required env var → set it in `.env`, then `docker compose up -d`.
- Migration partially applied → `docker compose exec backend alembic upgrade head`.

### Frontend shows "Invalid username or password" but creds are right
You're probably hitting the **idle timeout** (30 min default). Just log in
again. If it keeps happening, raise `SESSION_IDLE_TIMEOUT_SECONDS` in `.env`.

### "nexus_reachable: false" on the dashboard
- The backend container can't reach your Nexus. If Nexus is on the host, make
  sure `host.docker.internal` resolves (it's mapped via `extra_hosts` in
  `docker-compose.yml`).
- Verify the URL/port: `curl -u admin:<pw> http://<NEXUS_URL>/service/rest/v1/status`.
- Check `NEXUS_VERIFY_SSL`: if Nexus uses a self-signed cert, set it to `false`.

### Docker repositories don't show in the analyzer dropdown
The repo-list is cached for 30 s. Click **Refresh** next to the dropdown (it
hits `?refresh=true` and bypasses the cache). Also: the repo must exist in
Nexus first.

### Scanner reports "trivy binary not installed"
The backend image installs Trivy + Grype. If you built the image before
scanner support was added, rebuild: `docker compose build backend`. Verify:
`docker compose exec backend trivy --version`.

### Scan fails with registry auth error
The scanner uses `NEXUS_USERNAME` / `NEXUS_PASSWORD` to pull the image from
your Nexus registry. Make sure that account has read access on the docker
repository, and that `NEXUS_URL` resolves to the same host:port the registry
listens on (Nexus exposes the registry on the same port by default).

---

## Upgrading

1. Pull/checkout the new code.
2. Rebuild: `docker compose build`.
3. Apply any new migrations automatically: `docker compose up -d` (entrypoint
   runs `alembic upgrade head`).
4. Check `test/CHANGELOG.md` for breaking changes.
