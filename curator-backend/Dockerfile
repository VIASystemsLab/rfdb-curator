# curator-backend/Dockerfile
#
# Image for the FastAPI + uvicorn curator (write) backend.
#
# Both compose files build this one image. docker-compose.yml adds a source
# bind-mount (./curator-backend:/app) and uvicorn --reload for hot-reloading;
# docker-compose.prod.yml runs it as built, with no mount and several workers.

# Pin the version so `docker build` is fully reproducible.
# Update deliberately when you want to take a new Python release.
FROM python:3.12-slim

# uv installs every package, straight from uv.lock, so the image runs exactly
# the versions the suite was tested against and every download is checked
# against the lockfile's hash. Same uv version as local and CI (see
# .agent-defs/repo-specific.md, "Version pins").
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

# The base image's pip is never used, and it is old: its CA bundle still
# trusts the withdrawn emSign root. Removing it keeps it from being used by
# mistake and from being flagged in the shipped image.
RUN python -m pip uninstall --yes pip

# PYTHONUNBUFFERED: sends stdout/stderr straight to docker logs without
#   stdio buffering.  Without this, uvicorn output can be held in the buffer
#   and never appear if the process crashes before flushing.
#
# PYTHONDONTWRITEBYTECODE: stops Python from writing .pyc bytecode cache
#   files to disk.  Needed for the dev bind-mount: .pyc files written inside
#   the container would land on the HOST filesystem (./curator-backend),
#   owned by uid 1001 (appuser), where the host user cannot easily delete
#   them.  The cost is that every cold start, including in production,
#   compiles from source; the image does not pre-compile bytecode.
#
# UV_PROJECT_ENVIRONMENT: the virtualenv uv installs into. It lives OUTSIDE
#   /app on purpose: the dev compose bind-mount replaces /app wholesale, so a
#   virtualenv there would be shadowed at runtime. On PATH, so `uvicorn` in
#   CMD is the virtualenv's.
#
# UV_PYTHON_DOWNLOADS=never: use the image's Python, never fetch another.
# UV_LINK_MODE=copy: no uv cache is kept in the image, so nothing to link to.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

# The uv workspace (repo-root pyproject.toml and uv.lock) is rebuilt at
# /opt/rfdb, outside /app for the same reason as the virtualenv.
WORKDIR /opt/rfdb

# Third-party dependencies first, from the lockfile and the members'
# pyproject.toml files alone, so this layer stays cached until one of those
# changes. uv needs every member's pyproject.toml to read the workspace, but
# none of their code. --no-dev: the image gets runtime dependencies only.
COPY pyproject.toml uv.lock ./
COPY rfdb-core/pyproject.toml rfdb-core/
COPY curator-backend/pyproject.toml curator-backend/
COPY dataexplorer-backend/pyproject.toml dataexplorer-backend/
RUN uv sync --frozen --no-cache --no-dev --package rfdb-curator-backend --no-install-workspace

# The shared library (rfdb-core workspace member), installed EDITABLE, which
# is what makes `- ./rfdb-core:/opt/rfdb/rfdb-core` in docker-compose.yml work:
# the .pth written into the virtualenv points at this directory, so a
# bind-mount over it is picked up live. uv installs workspace members editable
# by default. The service itself is not a package and is not installed: its
# code is run from /app.
COPY rfdb-core/ rfdb-core/
RUN uv sync --frozen --no-cache --no-dev --package rfdb-curator-backend

WORKDIR /app

# Copy curator-backend source.  In development the compose bind-mount
# overlays this at runtime; this layer makes the image self-contained if
# run without a mount (e.g. in CI or production).
COPY curator-backend/ .

# Drop to a non-root user for defence-in-depth.
# python:slim does not ship a pre-created app user, so we create one
# explicitly with a fixed uid/gid for auditability.
RUN addgroup --system --gid 1001 appgroup && \
    adduser  --system --uid 1001 --ingroup appgroup --no-create-home appuser && \
    mkdir -p /app/logs && \
    chown -R appuser:appgroup /app/logs

USER appuser

EXPOSE 8000

# Production-safe default: no --reload.
# docker-compose.yml overrides this with --reload for local development.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
