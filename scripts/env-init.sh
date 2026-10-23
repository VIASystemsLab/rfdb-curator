#!/usr/bin/env bash
# scripts/env-init.sh — generate the DEV .env on first setup (runs on the HOST).
#
# WHEN TO RUN
#   Once, on a fresh checkout before the first `docker compose up`. It writes
#   the repo-root .env with freshly generated dev secrets.
#   Idempotent-ish: it REFUSES to clobber an existing .env unless you pass
#   --force (dev data is disposable, but an accidental regenerate would
#   invalidate the Garage key already imported into the cluster metadata).
#
# WHY IT EXISTS
#   .env is gitignored (it holds secrets), so a fresh checkout has none. The
#   dev stack needs the Garage RPC secret and the predefined S3 creds shared by
#   Garage (key import) and the backend. This mints them with openssl in the
#   exact format garage-init.sh and the backend expect.
#
# NEXT STEP
#   After this: `docker compose up -d`, then `scripts/garage-init.sh` once to
#   apply the layout, create the bucket, and import the key.
#
# PROD: do not use this file. See .env.prod.example + the prod bootstrap note.

set -euo pipefail

cd "$(dirname "$0")/.."          # repo root — where .env lives

FORCE=0
[ "${1-}" = "--force" ] && FORCE=1

if [ -e .env ] && [ "$FORCE" -ne 1 ]; then
  echo "✗ .env already exists — refusing to overwrite."
  echo "  Regenerating invalidates the S3 key already imported into Garage."
  echo "  Re-run with --force if you really want fresh dev secrets (then"
  echo "  'docker compose down -v' + scripts/garage-init.sh to re-bootstrap)."
  exit 1
fi

command -v openssl >/dev/null 2>&1 || { echo "✗ openssl not found on PATH"; exit 1; }

GARAGE_RPC_SECRET="$(openssl rand -hex 32)"          # 64 hex
S3_ACCESS_KEY_ID="GK$(openssl rand -hex 12)"         # GK + 24 hex
S3_SECRET_ACCESS_KEY="$(openssl rand -hex 32)"       # 64 hex

cat > .env <<EOF
# Dev-only secrets. Gitignored. Generated $(date +%F).
# Regenerate freely — dev data is disposable.

# Deploy mode. Read mode is the DEFAULT in docker-compose.yml — base services
# only, no editor — so day-to-day development sets this to get the whole stack
# from a bare \`docker compose up\`. Comment it out to run the read-only stack a
# public instance would run (or pass --profile full per invocation).
COMPOSE_PROFILES=full

# Project name: containers are rfdb-dev-<service>-1, volumes rfdb-dev_<volume>.
# Required by docker-compose.yml. Give another checkout on this machine its own.
COMPOSE_PROJECT_NAME=rfdb-dev

# Garage inter-node RPC secret (openssl rand -hex 32)
GARAGE_RPC_SECRET=$GARAGE_RPC_SECRET

# Predefined S3 creds shared by Garage (key import) and the backend.
# ID format: GK + 24 hex. Secret: 64 hex.
S3_ACCESS_KEY_ID=$S3_ACCESS_KEY_ID
S3_SECRET_ACCESS_KEY=$S3_SECRET_ACCESS_KEY
S3_BUCKET=sources
EOF

chmod 600 .env

echo "✓ wrote .env with fresh dev secrets"
echo "  next: docker compose up -d  &&  scripts/garage-init.sh"
