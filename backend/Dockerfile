# Backend image: FastAPI app + Alembic migrations + Trivy + Grype.
#
# All configuration is supplied via environment variables (docker-compose
# env_file). No secrets are baked into the image.
#
# The image contains scanner *binaries* only. It has no Docker client, no
# container runtime and (see docker-compose.yml) no Docker socket: images are
# read over the registry API and analysed statically, never executed.

# Scanner versions. Pinned, not floating: a scanner major release can change
# its database schema, at which point the database on the persisted cache
# volume stops validating and every scan fails until it is re-downloaded — and
# a floating tag means the CVE posture of the built image is not reproducible.
#
# `oras` previously tracked `main`, a moving development branch; it is now on a
# tagged release like the others.
#
# To move these forward deliberately, check the current releases with:
#   docker run --rm aquasec/trivy:latest --version
#   docker run --rm anchore/grype:latest version
#   docker run --rm ghcr.io/oras-project/oras:v1.2.2 version
# then bump the pin and rebuild. Override at build time if you need to test one:
#   docker compose build --build-arg TRIVY_VERSION=0.58.1 backend
ARG TRIVY_VERSION=0.58.1
ARG GRYPE_VERSION=v0.87.0
ARG ORAS_VERSION=v1.2.2

# BuildKit workaround: Define helper stages so variable expansion works
FROM aquasec/trivy:${TRIVY_VERSION} AS trivy-source
FROM anchore/grype:${GRYPE_VERSION} AS grype-source
FROM ghcr.io/oras-project/oras:${ORAS_VERSION} AS oras-source

# Pinned to a patch release + suite so the base OS package set — and therefore
# the image's CVE posture — is reproducible. `python:3.11-slim` alone silently
# moves under you between builds.
FROM python:3.11.11-slim-bookworm AS base

# Runtime deps: psycopg needs libpq5; ca-certificates + curl are needed to
# fetch the scanner install scripts and for the scanners to verify TLS when
# they pull their vulnerability DBs at runtime.
#
# `apt-get upgrade` picks up base-OS security updates published after the base
# image was cut — without it a pinned base tag means pinned known-vulnerable
# system libraries.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpq5 curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Scanner binaries, copied out of the official images.
# Using the upstream images as build sources (rather than curl-ing install
# scripts) keeps the build working behind a proxy and needs no extra tooling.
# ARGs are re-declared here: a global ARG is not visible inside a build stage.
# ---------------------------------------------------------------------------
COPY --from=trivy-source /usr/local/bin/trivy /usr/local/bin/trivy
COPY --from=grype-source /grype /usr/local/bin/grype
# oras: pulls the Trivy database, which ships as a plain OCI artifact.
COPY --from=oras-source /bin/oras /usr/local/bin/oras

# Fail the build now rather than at first scan if a binary did not come through.
RUN trivy --version && grype version && oras version

# ---------------------------------------------------------------------------
# Application user (non-root) + working directory
# ---------------------------------------------------------------------------
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching.
#
# pip/setuptools/wheel are upgraded first: the base image ships a setuptools
# affected by CVE-2024-6345 (code execution via package download) and
# CVE-2025-47273 (path traversal, fixed in 78.1.1). Those ship inside the
# image regardless of what requirements.txt asks for.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade "pip>=25.0" "setuptools>=78.1.1" wheel \
    && pip install --no-cache-dir --retries 6 --timeout 120 -r requirements.txt

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
