# curator-frontend/Dockerfile
#
# Three stages, two of them shipped:
#
#   development  docker-compose.yml     — Vite dev server + source bind-mount
#   build        (intermediate)         — npm run build, emits /app/dist
#   production   docker-compose.prod.yml — caddy:2.11-alpine serving that dist
#
# Both compose files name their stage with `target:` explicitly. Do not rely on
# stage order: Docker defaults to the LAST stage, so appending one silently
# repoints every build that omits `target`.

# ---------------------------------------------------------------------------
# development
# ---------------------------------------------------------------------------
# Pinned to major.minor (.agent-defs/repo-specific.md, "Version pins"):
# patch releases arrive on rebuild; a new minor is a deliberate change.
FROM node:26.12-bookworm-slim AS development

# NODE_ENV=development is not cosmetic: npm ci skips devDependencies when
# NODE_ENV=production, and vite is a devDependency. Pinning it here means
# the dev server installs correctly regardless of the ambient environment.
ENV NODE_ENV=development \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

WORKDIR /app

# Copy manifests first so `npm ci` is cached as long as package-lock.json
# doesn't change. --chown avoids a root-owned tree that would otherwise
# need a separate `RUN chown -R` layer later (which duplicates node_modules
# on overlay2 storage drivers via copy-up).
COPY --chown=node:node curator-frontend/package.json curator-frontend/package-lock.json ./

# WORKDIR creates /app as root before any USER switch. COPY --chown only
# changes ownership of the files it copies, not the /app directory entry —
# node still can't write into it (e.g. mkdir node_modules) without this.
RUN chown node:node /app


# Drop root before installing — nothing in a dependency's install script
# needs root in the build environment, and node_modules ends up correctly
# owned from the start.
USER node

# npm ci installs exactly what's in the lockfile and fails if it's out of
# sync with package.json. --no-audit skips an unnecessary registry call.
RUN npm ci --no-audit

# Copy the rest of the source. In development the compose bind-mount
# overlays this at runtime; this layer mainly makes the image
# self-contained if it's ever run without the mount.
# Requires curator-frontend/Dockerfile.dockerignore excluding **/node_modules
# and **/dist, otherwise a host node_modules (with host-arch native binaries, e.g.
# esbuild) will clobber the one just installed above.
COPY --chown=node:node curator-frontend/ .

EXPOSE 5173
# --host 0.0.0.0 makes the dev server reachable from outside the container.
# The compose `command:` key can override this if needed.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


# ---------------------------------------------------------------------------
# build — produces the static bundle
# ---------------------------------------------------------------------------
# Starts from `development` rather than repeating npm ci: the dependency layer
# is already there and already cached, and vite is a devDependency so the build
# needs exactly that install. (This is why NODE_ENV stays `development` here —
# it governs what npm installs, not what vite emits. `npm run build` produces a
# production bundle regardless, because Vite sets its own mode.)
FROM development AS build

# Vite inlines VITE_* at BUILD time — once built there is no server reading env
# per request, so these must be build args and NOT compose `environment:`
# entries. Changing one means rebuilding the image.
#
# Empty defaults are deliberate. The editor's client treats an empty base as
# "same origin", which is exactly right behind the edge proxy; a wrong non-empty
# default would ship a bundle pointing at someone else's host.
ARG VITE_API_BASE=""
ARG VITE_READ_API_BASE=""
ARG VITE_EXPLORER_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE \
    VITE_READ_API_BASE=$VITE_READ_API_BASE \
    VITE_EXPLORER_BASE=$VITE_EXPLORER_BASE

RUN npm run build


# ---------------------------------------------------------------------------
# production — static files only, no Node, no Vite
# ---------------------------------------------------------------------------
# caddy:2.11-alpine because the edge proxy already pins it: one fewer base image to
# track than an nginx variant, for the same `try_files` behaviour. The Node
# toolchain and node_modules do not survive into this stage, so the runtime
# attack surface is a file server and a directory of static assets.
FROM caddy:2.11-alpine AS production

COPY --from=build /app/dist /srv
# Shared with graphexplorer-frontend; the header of proxy/spa.Caddyfile says why
# it is one file rather than two copies.
COPY proxy/spa.Caddyfile /etc/caddy/Caddyfile

EXPOSE 80
# Inherits caddy's own CMD (`caddy run --config /etc/caddy/Caddyfile`), so there
# is nothing to repeat here.