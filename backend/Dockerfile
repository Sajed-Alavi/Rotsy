# Backend image: FastAPI app + Alembic migrations + Trivy + Grype + git + sonar-scanner.
#
# All configuration is supplied via environment variables (docker-compose
# env_file). No secrets are baked into the image.
#
# The image-scanning side (Trivy/Grype) contains scanner *binaries* only. It
# has no Docker client, no container runtime and (see docker-compose.yml) no
# Docker socket: Docker images are read over the registry API and analysed
# statically, never executed. That guarantee is specific to container-image
# scanning — it does not extend to the separate SonarQube module below, which
# legitimately needs `git` (to clone a source repository) and a JVM (to run
# `sonar-scanner`, itself a static source analyzer, same spirit as Trivy/Grype
# but for source code instead of image layers).

# Scanner versions. Pinned, not floating: a scanner major release can change
# its database schema, at which point the database on the persisted cache
# volume stops validating and every scan fails until it is re-downloaded — and
# a floating tag means the CVE posture of the built image is not reproducible.
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
#
# Then confirm the new binary still reads the database already on the volume,
# before rebuilding — this is the check that would have caught the above:
#   docker run --rm -v nexus-project_scanner-cache:/app/.cache \
#     -e GRYPE_DB_CACHE_DIR=/app/.cache/grype/db anchore/grype:<tag> db status
#
# Override at build time to test one: docker compose build --build-arg GRYPE_VERSION=v0.117.0 backend
#
# Bumped 2026-08-11 in response to a vulnerability scan of this image's own
# backend build (github.com/docker/docker, oras.land/oras-go/v2,
# golang.org/x/text, github.com/go-git/go-git/v5, google.golang.org/grpc,
# sigstore/cosign family — all Go deps embedded in these binaries at compile
# time, so a scanner-side pin bump is the only lever available; there is no
# separate "upgrade this one transitive dependency" step). NOT verified
# against the live cache volume the way the previous pin was — do that before
# trusting this build (see the check below), because a schema mismatch here
# is a repeat of the exact incident this comment block already documents.
ARG TRIVY_VERSION=0.73.0
ARG GRYPE_VERSION=v0.117.0
# sonar-scanner-cli's own image bundles a JRE (~150MB) we don't need — Alpine's
# own openjdk17-jre-headless (installed below) covers that, so only
# /opt/sonar-scanner itself (~10MB: the launcher script + sonar-scanner-cli
# 8.0.1.6346.jar) is copied out of this source stage. Verified 2026-08-06:
# this distribution has use_embedded_jre=false and its launcher script falls
# back to `JAVA_HOME/bin/java`, so pairing it with our own JRE instead of its
# bundled one is the documented, supported way to run it, not a hack.
#
# Pinned by digest rather than a version tag: unlike Trivy/Grype (each a
# single static binary with an obvious `--version` flag), this upstream image
# doesn't publish an easy-to-script tag list, so a digest pin is what was
# actually verified here — resolve a new one deliberately (`docker pull
# sonarsource/sonar-scanner-cli:<tag>` for the tag you want, then
# `docker inspect --format '{{.RepoDigests}}'`) rather than tracking `latest`.
ARG SONAR_SCANNER_DIGEST=sha256:23ca0f137965d9dff2198074043fd48d386280bc5d0ccac8c8349cea4cf096a9

# BuildKit workaround: Define helper stages so variable expansion works
FROM aquasec/trivy:${TRIVY_VERSION} AS trivy-source
FROM anchore/grype:${GRYPE_VERSION} AS grype-source
FROM sonarsource/sonar-scanner-cli@${SONAR_SCANNER_DIGEST} AS sonar-scanner-source

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
#
# `git` and a JRE are added for the Sonar module: `fetch_source` in
# modules/github/provider.py and modules/gitlab/provider.py shells out to
# `git clone`, and sonar-scanner (below) is a JVM tool. Neither was needed
# before that module existed, so neither was in this list — Rotsy's own
# analysis pipeline could never actually complete a clone or a scan without
# them, regardless of anything upstream (API auth, project setup) working.
RUN apk update && apk upgrade --no-cache \
    && apk add --no-cache \
        libpq ca-certificates \
        gcompat libstdc++ libgcc \
        git openjdk17-jre-headless \
    && rm -rf /var/cache/apk/*

# ---------------------------------------------------------------------------
# Scanner binaries, copied out of the official images.
# Using the upstream images as build sources (rather than curl-ing install
# scripts) keeps the build working behind a proxy and needs no extra tooling.
# ARGs are re-declared here: a global ARG is not visible inside a build stage.
# ---------------------------------------------------------------------------
COPY --from=trivy-source /usr/local/bin/trivy /usr/local/bin/trivy
COPY --from=grype-source /grype /usr/local/bin/grype
# sonar-scanner: see the ARG comment above — deliberately not its bundled JRE.
COPY --from=sonar-scanner-source /opt/sonar-scanner /opt/sonar-scanner

# JAVA_HOME so sonar-scanner's own launcher script (bin/sonar-scanner) finds
# the JRE installed above via `apk add openjdk17-jre-headless` instead of one
# it doesn't ship with; PATH so both binaries are runnable by name, matching
# how trivy/grype already are via /usr/local/bin.
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk
ENV PATH="/opt/sonar-scanner/bin:${JAVA_HOME}/bin:${PATH}"

# ---------------------------------------------------------------------------
# Application user (non-root) + working directory
# ---------------------------------------------------------------------------
# Explicitly set UID/GID to 999 so it matches Debian's default system user ID
# and retains read/write access to existing persisted Docker volumes.
# In Alpine, GID 999 is used by 'ping' by default, so we remove it first.
#
# Merged with the version check above (fail the build now rather than at
# first scan if a binary did not come through) — one RUN instead of two, with
# `delgroup`'s own `|| true` parenthesized so it cannot swallow a failure from
# the version-check chain ahead of it.
RUN trivy --version && grype version && git --version && sonar-scanner --version \
    && (delgroup ping 2>/dev/null || true) \
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

# Create the scanner cache dir and the backup output dir under /app so the
# non-root user can write to them (Trivy downloads its DB here at runtime;
# grype uses ~/.cache/grype; backups land in /app/backups, normally a mounted
# volume — creating and chowning the mountpoint here means Docker seeds a
# freshly-created named volume with the right ownership on first mount,
# instead of leaving it root-owned and un-writable by the `app` user).
# Merged with the entrypoint chmod above — one RUN instead of two.
RUN chmod +x ./entrypoint.sh \
    && mkdir -p /app/.cache/trivy /app/.cache/grype /app/backups \
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

# ---------------------------------------------------------------------------
# Test image — adds pytest and its deps on top of the runtime image.
#
# Not part of the default `docker compose up` (see the `backend-test` service
# in docker-compose.yml, gated behind the `test` profile): pytest,
# pytest-asyncio and aiosqlite have no reason to ship in the image that
# actually runs in production, the same "don't ship what you don't need"
# reasoning as the removed curl/gnupg above.
# ---------------------------------------------------------------------------
FROM base AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir --retries 6 --timeout 120 -r requirements-dev.txt
# base never copies these — pytest.ini/tests/ have no reason to be in the
# production image either, so they're only added here, in the test stage.
COPY pytest.ini ./
COPY tests/ ./tests/
RUN chown -R app:app /app/tests
USER app
CMD ["pytest"]
