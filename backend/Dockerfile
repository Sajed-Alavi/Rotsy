# Backend image: FastAPI app + Alembic migrations + Trivy + Grype.
#
# All configuration is supplied via environment variables (docker-compose
# env_file). No secrets are baked into the image.

FROM python:3.11-slim AS base

# Runtime deps: psycopg needs libpq5; ca-certificates + curl are needed to
# fetch the scanner install scripts and for the scanners to verify TLS when
# they pull their vulnerability DBs at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# External Tools via Multi-Stage Build
# Here we use the official images as "disposable containers" to extract the 
# binaries. This bypasses the need for curl and avoids network/proxy issues.
# ---------------------------------------------------------------------------
# Trivy
COPY --from=aquasec/trivy:latest /usr/local/bin/trivy /usr/local/bin/trivy

# Grype
COPY --from=anchore/grype:latest /grype /usr/local/bin/grype

# oras (OCI registry CLI used for DB pulls)
COPY --from=ghcr.io/oras-project/oras:main /bin/oras /usr/local/bin/oras

# Verify installations
RUN trivy --version && grype version && oras version

# ---------------------------------------------------------------------------
# Application user (non-root) + working directory
# ---------------------------------------------------------------------------
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 6 --timeout 120 -r requirements.txt

# Copy application + migration sources.
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Create the scanner cache dir under /app so the non-root user can write to it
# (Trivy downloads its DB here at runtime; grype uses ~/.cache/grype).
RUN mkdir -p /app/.cache/trivy /app/.cache/grype && chown -R app:app /app

# Switch to the non-root user.
USER app
ENV HOME=/app
ENV TRIVY_CACHE_DIR=/app/.cache/trivy
ENV GRYPE_CACHE_DIR=/app/.cache/grype

# uvicorn port (set via BACKEND_PORT at runtime).
EXPOSE 8000

# Run migrations + seed + uvicorn.
CMD ["./entrypoint.sh"]
