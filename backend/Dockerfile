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
# **A pin that is too old is its own outage.** These were first set to versions
# that were current knowledge rather than current releases, roughly eighteen
# months behind. Grype 0.87 reads database schema v5; Anchore stopped publishing
# v5 in March 2026 and serves v6 now, so that binary could no longer obtain a
# usable database at all — every scan failed with
# "database metadata not found: /app/.cache/grype/db/5" while a perfectly valid
# v6 database sat on the cache volume beside it.
#
# So: pin, but verify the pin against the *live* feed before shipping it, and
# revisit on every dependency pass. A scanner is only as good as the database it
# can still fetch.
#
# To move these forward deliberately, check the current releases with:
#   curl -s https://api.github.com/repos/anchore/grype/releases/latest | jq -r .tag_name
#   curl -s https://api.github.com/repos/aquasecurity/trivy/releases/latest | jq -r .tag_name
#   curl -s https://api.github.com/repos/oras-project/oras/releases/latest | jq -r .tag_name
#
# Then confirm the new binary still reads the database already on the volume,
# before rebuilding — this is the check that would have caught the above:
#   docker run --rm -v nexus-project_scanner-cache:/app/.cache \
#     -e GRYPE_DB_CACHE_DIR=/app/.cache/grype/db anchore/grype:<tag> db status
#
# Override at build time to test one: docker compose build --build-arg GRYPE_VERSION=v0.116.1 backend
#
# Verified against the live cache volume on 2026-08-01: grype v0.116.1 reports
# schema v6.1.9 "valid"; trivy 0.72.0 reports DB schema v2, unchanged from 0.58.1.
ARG TRIVY_VERSION=0.72.0
ARG GRYPE_VERSION=v0.116.1
ARG ORAS_VERSION=v1.3.3

# BuildKit workaround: Define helper stages so variable expansion works
FROM aquasec/trivy:${TRIVY_VERSION} AS trivy-source
FROM anchore/grype:${GRYPE_VERSION} AS grype-source
FROM ghcr.io/oras-project/oras:${ORAS_VERSION} AS oras-source

# Pinned to a patch release + suite so the base OS package set — and therefore
# the image's CVE posture — is reproducible. `python:3.13-alpine` alone
# silently moves under you between builds.
FROM python:3.13.14-alpine3.23 AS base

# Runtime deps: psycopg needs libpq; ca-certificates for TLS verification on
# outbound calls (Nexus REST API, database downloads via httpx). Neither curl
# nor gnupg are invoked anywhere in this app or entrypoint.sh — scanner
# binaries are copied from upstream images (see below), not fetched with curl
# via an install script, and nothing here shells out to gpg. They used to be
# installed "just in case"; carrying them bought nothing but their own CVEs
# (curl alone was ~40 of a 102-finding scan, with several unresolved CRITICALs
# since Alpine hadn't repatched this build yet — the only real fix available
# for an unused package is not shipping it).
#
# `apk upgrade` picks up base-OS security updates published after the base
# image was cut — without it a pinned base tag means pinned known-vulnerable
# system libraries.
RUN apk update && apk upgrade --no-cache \
    && apk add --no-cache \
        libpq ca-certificates \
        gcompat libstdc++ libgcc \
    && rm -rf /var/cache/apk/*

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
# Explicitly set UID/GID to 999 so it matches Debian's default system user ID
# and retains read/write access to existing persisted Docker volumes.
# In Alpine, GID 999 is used by 'ping' by default, so we remove it first.
RUN delgroup ping 2>/dev/null || true \
    && addgroup -g 999 -S app \
    && adduser -u 999 -S -G app -h /app app
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

# Create the scanner cache dir and the backup output dir under /app so the
# non-root user can write to them (Trivy downloads its DB here at runtime;
# grype uses ~/.cache/grype; backups land in /app/backups, normally a mounted
# volume — creating and chowning the mountpoint here means Docker seeds a
# freshly-created named volume with the right ownership on first mount,
# instead of leaving it root-owned and un-writable by the `app` user).
RUN mkdir -p /app/.cache/trivy /app/.cache/grype /app/backups \
    && chown -R app:app /app \
    && chmod -R 777 /app/.cache /app/backups

# Switch to the non-root user.
USER app
ENV HOME=/app
ENV TRIVY_CACHE_DIR=/app/.cache/trivy
ENV GRYPE_CACHE_DIR=/app/.cache/grype

# Grype's own download deadlines. Its defaults (5m for the database, 30s for the
# metadata) assume a fast link: the v6 database is ~139 MB, so a connection doing
# 150 KB/s needs about fifteen minutes and the default aborts it mid-stream.
# Grype then reports "unable to download db: unexpected EOF", which reads like a
# network fault rather than a timeout — the failure mode this pin exists to
# prevent. Lower them if your link is fast and you would rather fail quickly.
ENV GRYPE_DB_UPDATE_DOWNLOAD_TIMEOUT=45m
ENV GRYPE_DB_UPDATE_AVAILABLE_TIMEOUT=2m

# uvicorn port (set via BACKEND_PORT at runtime).
EXPOSE 8000

# Run migrations + seed + uvicorn.
CMD ["./entrypoint.sh"]
