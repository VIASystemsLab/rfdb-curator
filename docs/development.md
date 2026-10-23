# RFDB Curator — Development Workflow

This document covers development workflow for the standalone `rfdb-curator` project
(package names `rfdb-curator-backend` / `rfdb-dataexplorer-backend` / `rfdb-editor` internally in the member `pyproject.toml` files
and `curator-frontend/package.json`).

---

## 1. Environment Setup

### Python (uv workspace)

The repo is a **uv workspace** with three Python members — `rfdb-core/` (shared library),
`curator-backend/` (writes) and `dataexplorer-backend/` (reads). One lockfile and one
`.venv`, both at the **repo root**: a single sync installs every member, and an edit to
`rfdb-core` is picked up by both services with no reinstall.

```bash
cd <repo-root>
uv sync --all-extras --dev
source .venv/bin/activate
```

Use this one virtual environment for all Python development, tests, linting, and local
scripts. `cd`-ing into a member and running `uv sync` works identically — uv walks up to
the workspace root.

### Frontend

Dependencies are managed with `npm`, but Node never runs on the host: each frontend
image runs `npm ci` at build time, and every other `npm` command runs inside the
container, from the repo root.

```bash
docker compose build curator-frontend graphexplorer-frontend
docker compose up curator-frontend
```

Vite dev server runs on `http://localhost:5173`.

### Running the full application

Docker Compose is the preferred way to run both backends + both frontends + Oxigraph + Garage together during development.

On a fresh checkout, do the one-time setup first (requires OpenSSL): generate the gitignored `.env` and bootstrap Garage. Compose fails fast without `.env` (missing `GARAGE_RPC_SECRET`), and file uploads fail until Garage is bootstrapped.

```bash
# One-time, from the repository root
scripts/env-init.sh          # generate .env with fresh dev secrets
docker compose up -d --build
scripts/garage-init.sh        # bootstrap Garage layout/bucket/key (re-run after any `down -v`)
```

Both scripts are idempotent. After setup, ordinary runs need only:

```bash
docker compose up --build
```

`env-init.sh` writes `COMPOSE_PROFILES=full` into `.env`, which is what makes that command
bring up all six services. Without it — an `.env` that predates the deploy profiles, for
instance — you get the read-only four and no editor on `:5173`. See
[deployment.md](deployment.md#deploy-modes--read-vs-full); the short version is that a bare
`docker compose up` is the mode a public instance runs, and development opts into the write
tier.

### Hot reload

Both services use bind-mounts in Docker Compose, so source changes reload automatically without rebuilding images:

- Backend: uvicorn `--reload` watches `/app`
- Frontend: Vite dev server watches `/app`

---

## 2. Code Quality

### Tools

- **Ruff** — Python linting and formatting
- **ESLint** — React/JS linting
- **Prettier** — frontend formatting
- **pre-commit** — file hygiene and pre-commit checks

Recommended pre-commit checks: detect unresolved merge-conflict markers, validate TOML/YAML/JSON, normalize final newlines, strip trailing whitespace, run Ruff across every Python workspace member, run frontend lint/format checks.

### Local commands


Run directly:

```bash
# Backend
cd curator-backend
ruff check .
ruff format .
python -m pytest

# Frontend, in the container, from the repo root
docker compose run --rm --no-deps curator-frontend npm run lint
docker compose run --rm --no-deps curator-frontend npm run build
```

### pre-commit setup

All commit checks run from `.pre-commit-config.yaml`: the commit message format, no
machine-local absolute paths, ruff check, ruff format, and prefix-map coverage. The
scripts behind the first two live in `scripts/hooks/`. Install the hooks once per clone;
the one command installs the `commit-msg` hook too:

```bash
uv tool install pre-commit
pre-commit install
```

Run all hooks manually:

```bash
pre-commit run --all-files
```

pre-commit is meant to catch formatting/hygiene issues before CI does.

---

## 3. CI/CD Expectations

**Backend checks:**
- install deps with `uv`
- Ruff lint
- Ruff format check
- run tests
- verify SHACL schema parses
- (optional) validate seed data against the active SHACL schema

**Frontend checks** (on the CI runner, not a developer host):
- `npm ci`
- ESLint
- production build

**Integration checks (if practical):**
- start Oxigraph
- start both backends
- check `/health` on :8000 **and** :8001 (the payloads differ — the writer reports
  `oxigraph` + `seed`, the reader reports `store`)
- check `/api/v1/dataexplorer/shapes` on both, and `/api/v1/dataexplorer/entities` on :8001
- run a dry-run validation request against :8000

---

## 4. Schema Change Workflow

Forms come from the SHACL schema at `SCHEMA_PATH`. In this repository that is
`schema/rfdb_schema.ttl`, a copy of a tagged [rfdb-schema](https://github.com/VIASystemsLab/rfdb-schema)
release: a model change is made upstream, released, and copied in by the procedure in
[schema/README.md](../schema/README.md), never by editing the copy. To try a model of your
own, point `SCHEMA_PATH` at it. Each record type is a `sh:NodeShape`.

When a schema adds or changes shapes, verify:

`sh:targetClass`, `rdfs:label`, `sh:description`, `sh:property`, `sh:path`, `sh:minCount`, `sh:maxCount`, `sh:datatype`, `sh:nodeKind`, `sh:class`, `sh:node`, `sh:or`, `sh:hasValue`, `sh:closed`, `sh:uniqueLang`

The editor discovers updated shapes through the shared schema extractor
(`rfdb-core/rfdb_core/schema_extractor.py`), which both services load at startup:

```
GET /api/v1/dataexplorer/shapes            # both services; the curator's adds the readOnly flag
GET /api/v1/curator/forms?shapeId=... # curator only — form definitions drive an editing form
```

A schema change therefore needs **both** services restarted: each parses the schema
into its own in-memory index at startup.

Two behaviors matter when adding or changing a shape, and both are documented elsewhere:

- **Helper/bridge vs. standalone-entity classification** — whether a shape renders as a top-level record or as a nested inline editor is derived from the schema (whether it declares an `rdfs:label` property), not hardcoded per class name. Check both the SHACL shape and the resulting `/api/v1/dataexplorer/shapes` metadata before changing frontend code. See [architecture.md](architecture.md#helperbridge-shape-classification).
- **Modeling rationale** — why the rfdb model uses `cidoc:P19_was_intended_use_of` and `cidoc:P16_used_specific_object` side by side, and `dcterms:contributor` with `rfdbs:ContributorShape`. These belong to the model, so they are documented upstream, in [data-model.md](https://github.com/VIASystemsLab/rfdb-schema/blob/v2.2.2/docs/data-model.md).

---

## 5. Data Change Workflow

- `data/rfdb_vocab.ttl` — canonical seed data for controlled vocabulary. Seeding should normally be **enabled**.
- `data/rfdb_data.ttl` — test-only fixture data. Seeding should normally be **disabled** outside dev/test.

### Namespace hardcoding map

When the RFDB namespaces change, update these locations together:

- `schema/rfdb_schema.ttl` — authoritative Turtle prefixes and SHACL shape IRIs. Shape IRIs use `rfdbs:` (`https://rosfeatr.eu/rdf/schema/`); data resources use `rfdb:` (`https://rosfeatr.eu/rdf/data/`).
- `data/rfdb_vocab.ttl` and `data/rfdb_data.ttl` — authoritative Turtle data prefixes for seeded and fixture data.
- `DATA_BASE_URI` and `SCHEMA_BASE_URI` — the settings both backends name records and shapes under. Set them in the compose files (or a root `.env` in dev, `.env.prod` in production); the `.env.example` files show where each one ends up.
- `curator-backend/models/data.py`, `rfdb-core/rfdb_core/models_data.py` and the two `core/config.py` files — doc examples reference shape/data IRIs.
- `tests/` Python files (all three subdirectories) — many tests assert full shape or entity IRIs directly; search for `rosfeatr.eu/rdf/` before and after any namespace migration.

Quick check from repo root:

```bash
rg -n 'https://rosfeatr\.eu/rdf/|https://rfdb\.it/data/' schema data curator-backend rfdb-core tests docs AGENTS.md .agent-defs
```

---

## 6. Troubleshooting

### Oxigraph not ready

```bash
docker compose logs oxigraph
docker compose ps
```

Confirm port `7878` is free and the container is `healthy`.

### Backend refuses to start

```bash
docker compose logs curator-backend
```

Check that all required environment variables are present and valid.

### CORS errors

`CORS_ORIGINS` must include the frontend origin, e.g.:

```json
["http://localhost:5173"]
```

Do not use `*` when credentials are enabled — the app rejects `CORS_ORIGINS=["*"]` when `allow_credentials=True`.

### Seed failures

- Confirm `SCHEMA_PATH`, `VOCAB_PATH`, `DATA_PATH` resolve correctly inside the container.
- Confirm the `schema/` and `data/` bind-mounts are present in `docker-compose.yml`.
- Host files must be world-readable if your umask is restrictive.

### SHACL validation failures

Check, in order:

- missing required fields
- wrong literal datatypes
- invalid IRIs
- missing `@type` values
- broken links to referenced entities
- cardinality violations
- language-tag issues, incl. duplicate tags where `sh:uniqueLang true`
- missing linked records required by `sh:class`/`sh:node`
- closed-shape violations where `sh:closed true`

**Nuance:** shapes using `sh:targetClass` only apply to nodes that explicitly declare the matching RDF class. For JSON-LD payloads, this means required `@type` values must be preserved — especially on helper/bridge nodes such as `core:AgentRole`.

### Log locations

Runtime logs: `/app/logs/app.jsonl` inside each backend container (persisted in the
`curator_backend_logs` / `dataexplorer_backend_logs` named volumes). Container
stdout/stderr:

```bash
docker compose logs curator-backend
docker compose logs curator-frontend
```

---

## 7. Commit Workflow

Before committing:

```bash
uv run ruff check . && uv run ruff format --check .   # from the repo ROOT
cd rfdb-core               && uv run python -m pytest -c pyproject.toml ../tests/core
cd ../curator-backend      && uv run python -m pytest -c pyproject.toml ../tests/curator
cd ../dataexplorer-backend && uv run python -m pytest -c pyproject.toml ../tests/dataexplorer
cd .. && docker compose run --rm --no-deps curator-frontend sh -c 'npm run lint && npm run build'
pre-commit run --all-files   # if configured
```

Then:

```bash
 # Never use `git add .` — always review changes first
git add [ ...files... ]  
git commit -m "Describe the change"
```

A good PR description covers: what changed, whether the SHACL schema changed, whether seed data changed, whether validation behavior changed, whether frontend form behavior changed, and any migration/compatibility implications.

---

## 8. Quick Regex Log Checks (when UI submit appears to do nothing)

Backend structured logs are written to `/app/logs/app.jsonl` inside the
`curator_backend_logs` volume (the reader has its own, `dataexplorer_backend_logs` —
swap the volume name in the commands below to inspect it). You can query them without
entering a container:

```bash
docker run --rm -v rfdb_backend_logs:/logs alpine ls -l /logs
```

Start with broad error scan:

```bash
docker run --rm -v rfdb_backend_logs:/logs alpine sh -lc \
	"rg -n -i 'error|exception|traceback|validation|shacl|httpexception' /logs/app.jsonl"
```

Check whether POST `/api/v1/curator/entities` was hit at all:

```bash
docker run --rm -v rfdb_backend_logs:/logs alpine sh -lc \
	"rg -n 'POST|/api/v1/curator/entities|create_or_update_entity|validationReport' /logs/app.jsonl"
```

Target your example payload text (`Expression 1`, `en`):

```bash
docker run --rm -v rfdb_backend_logs:/logs alpine sh -lc \
	"rg -n -i 'Expression 1|\"en\"|rdfs:label|core:text|langString' /logs/app.jsonl"
```

Live-tail only relevant lines while retrying submit in UI:

```bash
docker run --rm -v rfdb_backend_logs:/logs alpine sh -lc \
	"tail -f /logs/app.jsonl" | rg --line-buffered -i 'POST|/api/v1/curator/entities|Expression 1|validation|error|exception|shacl'
```

If using Docker and file logs look empty, check container logs too:

```bash
docker compose logs -f curator-backend | rg --line-buffered -i 'POST|/api/v1/curator/entities|validation|error|exception|shacl|Expression 1'
```

Tip: if no `POST /api/v1/curator/entities` pattern appears when clicking save, the request likely never left the browser (frontend-side issue). If POST appears with SHACL or validation errors, it is a curator-backend/schema rejection. If a *read* seems to fail, tail `dataexplorer-backend` instead — the two services log to separate volumes.

