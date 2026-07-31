# Frontend image: multi-stage build (node -> nginx).
#
# Stage 1 compiles the Vite/React bundle, baking in VITE_* build-time vars.
# Stage 2 serves the static bundle with nginx, reverse-proxying /api to the
# backend container. Runtime config (BACKEND_UPSTREAM) is envsubst'd into
# nginx.conf at container start.

# ---------- Stage 1: build ----------
# Node 22: required by the toolchain, not chosen freely — vite 8 needs
# ^20.19.0 || >=22.12.0 and react-router 8 needs >=22.22.0. Pinned to a patch
# release so the build is reproducible.
FROM node:22.23.2-alpine AS build

WORKDIR /app

# Install dependencies first for layer caching.
#
# `npm ci` (not `npm install`): it installs exactly what package-lock.json
# resolves and fails loudly when the lockfile and package.json disagree, rather
# than silently resolving something else. Regenerate the lockfile with
# `npm install` locally whenever package.json changes, and commit it.
COPY package.json package-lock.json ./
RUN npm ci

# Copy sources and build. VITE_* env vars are read at build time.
COPY . .
RUN npm run build

# ---------- Stage 2: serve ----------
FROM nginx:1.27.3-alpine AS serve

# Run nginx as a non-root user. The nginx alpine image ships an unprivileged
# user "nginx"; we listen on port 80 inside the container, which is fine
# because the container itself runs non-root and is published via compose.
#
# Note: binding to port 80 as non-root requires either capability
# NET_BIND_SERVICE or using a high port. The nginx:alpine image already
# configures the PID path and worker to support running non-root; to keep the
# OSS setup simple and portable we publish via compose (host ${FRONTEND_PORT}
# -> container 80) and document the capability in the README.

# envsubst template directory (the nginx image processes /etc/nginx/templates/*).
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Static bundle from the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# The nginx image's default entrypoint runs envsubst then starts nginx.
CMD ["nginx", "-g", "daemon off;"]
