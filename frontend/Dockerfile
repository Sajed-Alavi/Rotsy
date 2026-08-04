# Frontend image: multi-stage build (node -> nginx).
#
# Stage 1 compiles the Vite/React bundle, baking in VITE_* build-time vars.
# Stage 2 serves the static bundle with nginx, reverse-proxying /api to the
# backend container. Runtime config (BACKEND_UPSTREAM) is envsubst'd into
# nginx.conf at container start.

# ---------- Stage 1: build ----------
# Node 24 (current LTS "Krypton", supported through April 2028): satisfies the
# toolchain floor — vite 8 needs ^20.19.0 || >=22.12.0 and react-router 8 needs
# >=22.22.0 — with headroom, and tracks upstream LTS rather than sitting on the
# minimum. Pinned to a patch release so the build is reproducible.
FROM node:24.16.0-alpine AS build

WORKDIR /app

# Install dependencies first for layer caching.
#
# `npm ci` (not `npm install`): it installs exactly what package-lock.json
# resolves and fails loudly when the lockfile and package.json disagree, rather
# than silently resolving something else. Regenerate the lockfile with
# `npm install` locally whenever package.json changes, and commit it.
COPY package.json package-lock.json ./
RUN npm install

# Copy sources and build. VITE_* env vars are read at build time.
COPY . .
RUN npm run build

# ---------- Stage 2: serve ----------
FROM nginx:1.28-alpine-slim AS serve

# Run nginx as a non-root user. The nginx alpine image ships an unprivileged
# user "nginx"; we listen on port 80 inside the container, which is fine
# because the container itself runs non-root and is published via compose.
#
# Note: binding to port 80 as non-root requires either capability
# NET_BIND_SERVICE or using a high port. The nginx:alpine image already
# configures the PID path and worker to support running non-root; to keep the
# OSS setup simple and portable we publish via compose (host ${FRONTEND_PORT}
# -> container 80) and document the capability in the README.

# Upgrade already-installed OS packages to patch OpenSSL (libcrypto3/libssl3),
# libxml2, libpng and xz-libs, and remove curl to shrink the attack surface.
#
# nginx itself is pulled explicitly from Alpine's own `main` repo rather than
# relying on `apk upgrade` against whatever this base image's
# /etc/apk/repositories already points at (nginx.org's own channel, pinned
# there at this base image's build time). CVE fixes for nginx land as Alpine
# package-revision bumps on the existing version (e.g. nginx-1.28.3-r1 ->
# -r6 for CVE-2026-42533), not a new nginx.org release, so nginx.org's repo
# does not carry them — only Alpine's does. `--repository` adds that repo for
# this one install without permanently rewriting /etc/apk/repositories.
# `latest-stable` is Alpine's own always-current alias, so this keeps
# tracking fixes rather than freezing on whatever branch existed when this
# line was written.
RUN apk update && apk upgrade --no-cache \
    && apk add --no-cache --upgrade \
       --repository=https://dl-cdn.alpinelinux.org/alpine/latest-stable/main \
       nginx \
    && mkdir -p /run/nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /etc/nginx/templates \
    && chown -R nginx:nginx /run/nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /etc/nginx/templates /tmp \
    && (apk del --no-cache curl || true) \
    && rm -rf /var/cache/apk/*

# Alpine's nginx package overwrote /etc/nginx/nginx.conf with its own default
# top-level config (installed above, after the `nginx.org`-pinned image's own
# version) — this puts our known-good one back, with pid already pointing at
# /tmp/nginx.pid (see nginx-main.conf's own comment for why).
COPY --chown=nginx:nginx nginx-main.conf /etc/nginx/nginx.conf

# envsubst template directory (the nginx image processes /etc/nginx/templates/*).
COPY --chown=nginx:nginx nginx.conf /etc/nginx/templates/default.conf.template
COPY --chown=nginx:nginx --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

# The nginx image's default entrypoint runs envsubst then starts nginx.
CMD ["nginx", "-g", "daemon off;"]
