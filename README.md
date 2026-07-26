# Nexus Repository Manager — Advanced Web Wrapper

A fast, modern, lightweight web UI and management wrapper around
**Sonatype Nexus Repository Manager**. Built as two standalone projects:

- **`backend/`** — Python / FastAPI management API.
- **`frontend/`** — React + Vite + Tailwind single-page app.

> ⚠️ **Security model.** There are **no hardcoded defaults** anywhere in the
> codebase. Every URL, credential, port, and tuning value is read from
> environment variables. The application fails fast at startup if anything
> required is missing. Never commit a real `.env` file.

## Features

| | Feature | Status |
|---|---|---|
| **A** | Deep Storage Analyzer — bypasses multi-arch manifests, reports physical layer sizes and wasted/orphaned disk space | ✅ Implemented |
| **B** | Advanced Retention & Cleanup Automation (dry-run + execute) | 🚧 Scaffolded |
| **C** | Blobstore Management | 🚧 Scaffolded |
| **D** | System Update & Host Script Triggering | 🚧 Scaffolded |
| **E** | Open-Source Vulnerability Scanning (Trivy/Grype) | 🚧 Scaffolded |
| **F** | Advanced Repository & Proxy Management | 🚧 Scaffolded |
| **G** | CI/CD Tokens & Webhooks | 🚧 Scaffolded |
| **H** | Observability & Analytics | 🚧 Scaffolded |

Scaffolded features expose empty route placeholders and a consistent
"coming soon" UI panel so the architecture is in place.

## Quick start (Docker Compose)

```bash
cp .env.example .env
# edit .env: set NEXUS_URL, NEXUS_USERNAME, NEXUS_PASSWORD,
#            NEXUS_UI_API_KEY, VITE_API_KEY (make them match)
docker compose up --build
```

Then open <http://localhost:8080>.

## Architecture

```
browser ──HTTP──► nginx (frontend container)
                     │  /api/*  ──proxy──►  FastAPI (backend container)
                     │                            │
                     │                            ├──► Nexus REST + Docker APIs
                     │                            └──► Redis (cache)
                     └── serves React SPA
```

- **Caching:** Redis with TTL. Analyzer results, repo/blobstore lists, and
  health are cached so the dashboard is faster than the native Nexus UI.
- **Real-time:** the Deep Storage Analyzer streams progress over
  **Server-Sent Events (SSE)** — long-running scans update the UI live.

## Local development (without Docker)

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# export the vars from .env (or use a tool like direnv)
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev     # http://localhost:5173  (Vite proxies /api -> :8000)
```

## Configuration

See [`.env.example`](./.env.example) for the full, documented list of
environment variables. Key points:

- `NEXUS_URL` / `NEXUS_USERNAME` / `NEXUS_PASSWORD` — Nexus connection.
- `NEXUS_UI_API_KEY` — secret the frontend sends as `X-API-Key` to the
  backend. Must equal the build-time `VITE_API_KEY`.
- `NEXUS_VERIFY_SSL` — keep `true` in production.
- `REDIS_URL`, `CACHE_TTL_SECONDS` — cache.
- `ANALYZER_MAX_CONCURRENCY`, `ANALYZER_REQUEST_TIMEOUT` — analyzer tuning.

## Project layout

```
nexus-project/
├── .env.example
├── docker-compose.yml
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # FastAPI app, lifespan, CORS, router wiring
│       ├── config.py          # pydantic-settings (no defaults)
│       ├── dependencies.py    # settings, cache, nexus client, API-key guard
│       ├── core/              # nexus_client, cache, sse helpers
│       ├── routers/           # health, storage, retention, ... (A full, B–H stubs)
│       └── services/
│           └── storage_analyzer.py   # Feature A — refactored CLI script
└── frontend/
    ├── Dockerfile             # multi-stage node -> nginx
    ├── nginx.conf             # SPA + /api proxy (envsubst)
    ├── package.json
    └── src/
        ├── api/  utils/  components/  pages/
```

## Security checklist

- [x] No hardcoded secrets/URLs/ports — every value is an env var.
- [x] Management API protected by `X-API-Key`, independent of Nexus creds.
- [x] Non-root users in both Docker images.
- [x] `.env` git-ignored; only `.env.example` tracked.
- [ ] Per-user auth / OIDC — future work for multi-tenant hosting.

## License

MIT (see `LICENSE` when added before public release).
