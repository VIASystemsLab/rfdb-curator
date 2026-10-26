# graphexplorer-frontend/Dockerfile
#
# Three stages, mirroring curator-frontend/Dockerfile — see that file for the
# reasoning, which is identical:
#
#   development  docker-compose.yml      — Vite dev server + source bind-mount
#   build        (intermediate)          — npm run build, emits /app/dist
#   production   docker-compose.prod.yml — caddy:2-alpine serving that dist
#
# Both compose files name their stage with `target:` explicitly; Docker's default
# is the LAST stage, so never rely on order.

# ---------------------------------------------------------------------------
# development
# ---------------------------------------------------------------------------
# Pin the full patch version so `docker build` is reproducible.
FROM node:26.12-bookworm-slim AS development

# npm ci skips devDependencies when NODE_ENV=production, and vite is a
# devDependency — pin development so the dev server installs correctly.
ENV NODE_ENV=development \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

WORKDIR /app

# Copy manifests first so `npm ci` is cached while the lockfile is unchanged.
COPY --chown=node:node graphexplorer-frontend/package.json graphexplorer-frontend/package-lock.json ./

# WORKDIR creates /app as root; hand it to node so it can write node_modules.
RUN chown node:node /app

USER node

RUN npm ci --no-audit

# Copy the rest; the compose bind-mount overlays this at runtime. Requires
# graphexplorer-frontend/Dockerfile.dockerignore excluding **/node_modules so a
# host node_modules can't clobber the container's native binaries.
COPY --chown=node:node graphexplorer-frontend/ .

EXPOSE 5174
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


# ---------------------------------------------------------------------------
# build — produces the static bundle
# ---------------------------------------------------------------------------
FROM development AS build

# Vite inlines VITE_* at BUILD time, so these are build args, never compose
# `environment:` entries. Empty API base = same origin, which is what the edge
# proxy provides.
ARG VITE_API_BASE=""
# Entity-page link base, read as READ_BASE in src/api/client.js. Empty = same
# origin, which is what the edge proxy provides; set it only for a build whose
# reader sits on another origin.
ARG VITE_READ_API_BASE=""
ARG VITE_APP_TITLE="RFDB Explorer"

# The one arg the editor does not need. Production serves this app under a path
# (`/explorer/`), and Vite bakes asset URLs against `base` at build time — left
# at `/` the bundle would request /assets/… and get the *editor's* index.html
# from the edge proxy's fallback route, which fails as a blank page rather than
# a 404. Defaults to `/` so a standalone deploy at a domain root still works.
ARG VITE_BASE_PATH="/"

ENV VITE_API_BASE=$VITE_API_BASE \
    VITE_READ_API_BASE=$VITE_READ_API_BASE \
    VITE_APP_TITLE=$VITE_APP_TITLE \
    VITE_BASE_PATH=$VITE_BASE_PATH

RUN npm run build


# ---------------------------------------------------------------------------
# production — static files only, no Node, no Vite
# ---------------------------------------------------------------------------
FROM caddy:2-alpine AS production

COPY --from=build /app/dist /srv
# The same file curator-frontend's production stage copies — one static-server
# config for both apps.
COPY proxy/spa.Caddyfile /etc/caddy/Caddyfile

EXPOSE 80
