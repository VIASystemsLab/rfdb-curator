# dataexplorer-backend/Dockerfile
#
# Image for the FastAPI + uvicorn read backend.
#
# Deliberately the curator image with the writer bits removed: same base, same
# layer order, same non-root user. Keeping them structurally identical means a
# fix to one is an obvious copy to the other; the differences that matter are in
# requirements.txt (no pyshacl/pypdf/python-multipart) and the port.
#
# Both compose files build this one image. docker-compose.yml adds a source
# bind-mount (./dataexplorer-backend:/app) and uvicorn --reload for hot-reloading;
# docker-compose.prod.yml runs it as built, with no mount and several workers.

# Pin the version so `docker build` is fully reproducible.
# Update deliberately when you want to take a new Python release.
FROM python:3.12-slim

# PYTHONUNBUFFERED: sends stdout/stderr straight to docker logs without
#   stdio buffering.  Without this, uvicorn output can be held in the buffer
#   and never appear if the process crashes before flushing.
#
# PYTHONDONTWRITEBYTECODE: stops Python from writing .pyc bytecode cache
#   files to disk.  Needed for the dev bind-mount: .pyc files written inside
#   the container would land on the HOST filesystem (./dataexplorer-backend),
#   owned by uid 1001 (appuser), where the host user cannot easily delete
#   them.  The cost is that every cold start, including in production,
#   compiles from source; the image does not pre-compile bytecode.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1


WORKDIR /app


# The base image's pip vendors a CA bundle that still trusts the withdrawn
# emSign root, so every download it makes is open to a man-in-the-middle.
# Replace it before anything else is installed. The wheel is pinned by
# sha256, which the old pip checks, so this first download is verified too.
RUN pip install --no-cache-dir "pip @ https://files.pythonhosted.org/packages/07/25/ee8c38ae72d66b844ce8eb9711ba905dba21ca1a8a0c960057c41cd23337/pip-26.3-py3-none-any.whl#sha256=f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13"

# Copy and install dependencies before the source so this layer is cached
# as long as requirements.txt does not change.
#
# Security note: for stronger supply-chain guarantees, generate your
# requirements.txt with `pip-compile --generate-hashes` and add
# `--require-hashes` here so the build fails if any package has been
# tampered with since the lockfile was created.
COPY dataexplorer-backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The shared library (rfdb-core workspace member).  It lives OUTSIDE /app on
# purpose: the compose bind-mount for hot-reload replaces /app wholesale, so
# anything installed there would be shadowed at runtime.
#
# Installed EDITABLE, which is what makes `- ./rfdb-core:/opt/rfdb-core` in
# docker-compose.yml work: the .pth written into site-packages points at this
# directory, so a bind-mount over it is picked up live.  A regular
# (non-editable) install would copy the sources into site-packages and the
# bind-mount would silently have no effect.
#
# --no-deps: rfdb-core's runtime pins are a subset of requirements.txt above
# (the workspace resolves one version per package across all members), so
# re-resolving them here could only introduce drift.
COPY rfdb-core/ /opt/rfdb-core/
RUN pip install --no-cache-dir --no-deps -e /opt/rfdb-core

# Copy dataexplorer-backend source.  In development the compose bind-mount
# overlays this at runtime; this layer makes the image self-contained if
# run without a mount (e.g. in CI or production).
COPY dataexplorer-backend/ .

# Drop to a non-root user for defence-in-depth.
# python:slim does not ship a pre-created app user, so we create one
# explicitly with a fixed uid/gid for auditability.
RUN addgroup --system --gid 1001 appgroup && \
    adduser  --system --uid 1001 --ingroup appgroup --no-create-home appuser && \
    mkdir -p /app/logs && \
    chown -R appuser:appgroup /app/logs

USER appuser

EXPOSE 8001

# Production-safe default: no --reload.
# docker-compose.yml overrides this with --reload for local development.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
