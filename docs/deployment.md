# Deployment & Operations — RossijskijFeatrDB

This document covers three things:

1. **[Development & Testing Deployment](#development--testing-deployment)** — how the
   local `docker-compose.yml` stack is configured, seeded, and reset. This is the
   detailed reference behind the lean [Quick Start](../README.md#quick-start) in the root
   README (which covers only the commands to get running).
2. **[Deploy modes](#deploy-modes--read-vs-full)** — read-only vs. full, selected by
   Compose profile in **either** compose file. Applies to dev and production alike.
3. **[Production Deployment](#production-deployment)** — the runbook.

---

## Development & Testing Deployment

The development and testing stack is the `docker-compose.yml` at the repository root. Get
it running with the [Quick Start](../README.md#quick-start); the sections below document
the configuration knobs, the data-reset modes, and the seed sources it uses.

> ⚠ **A bare `docker compose up` does not start the editor.** Read mode is the default in
> both compose files (see [Deploy modes](#deploy-modes--read-vs-full)). For the whole
> stack, set `COMPOSE_PROFILES=full` in the repo-root `.env`, which `scripts/env-init.sh`
> writes; it refuses to overwrite an existing `.env`, so an `.env` written another way
> needs the line added by hand. Or pass `--profile full` on each invocation.

### Configuration

All settings are loaded from environment variables. No defaults are hardcoded in the
Python source; each service fails fast with a clear validation error if a required
variable is missing or has the wrong type.

Configuration source of truth:

- Runtime wiring: the two `environment:` blocks in `docker-compose.yml`
- Shared settings base: `rfdb-core/rfdb_core/config.py` (`BaseServiceSettings`)
- Writer settings: `curator-backend/core/config.py` — the base plus the write surface
- Reader settings: `dataexplorer-backend/core/config.py` — the base, unchanged
- Dependency sets: `curator-backend/pyproject.toml`, `dataexplorer-backend/pyproject.toml`

**Which variables apply to which service.** The table below marks each one. The base
ignores unknown variables, so a single shared `.env` is safe: the writer-only values are
simply invisible to the reader — setting `READ_ONLY=true` has no effect on
`dataexplorer-backend`, because it has no writes to disable.

For Docker Compose, edit the relevant `environment:` block. `OXIGRAPH_URL` uses the
Docker-internal hostname since the store is a separate service; `SCHEMA_PATH` /
`VOCAB_PATH` / `DATA_PATH` are container paths backed by the `volumes:` mounts (change
both together). `OXIGRAPH_URL`, `DATA_GRAPH_URI`, `DATA_BASE_URI` and
`SCHEMA_BASE_URI` **must match across the two services**, or the reader will report
an empty store or fail to find what the writer minted.

| Variable | Service | Required | Description |
|---|---|---|---|
| `COMPOSE_PROJECT_NAME` | compose | Yes | Compose project name, required by both compose files: containers are `<name>-<service>-1`, volumes `<name>_<volume>`. `rfdb-dev` in the repo-root `.env` (written by `scripts/env-init.sh`), `rfdb-prod` in `.env.prod`. Each stack on a host needs its own; changing it on a running deployment starts new, empty volumes. |
| `TRIPLESTORE` | both | No | Which `TripleStore` implementation to build. Only `oxigraph` ships today; the seam exists so a second one is a config change. Default: `oxigraph`. |
| `OXIGRAPH_URL` | both | Yes | Base URL of the Oxigraph HTTP endpoint, no trailing slash. `http://localhost:7878` locally, `http://oxigraph:7878` inside Docker. Must match across services. |
| `DATA_GRAPH_URI` | both | Yes | Named graph URI where instance data is stored and queried. Every SPARQL read uses `FROM <uri>`; every Turtle load targets `?graph=<uri>`. Must match across services. |
| `DATA_BASE_URI` | both | Yes | Namespace every minted record and digital copy is named under, ending in `/` or `#`. With `https://rosfeatr.eu/rdf/data/`, a new Place is `https://rosfeatr.eu/rdf/data/Place_4f3a9d12c07e` and a PDF's `schema:contentUrl` is `…/data/File_0123abcd0123/content`. Served to the curator frontend on `GET /api/v1/dataexplorer/meta/schema`. Must match across services. |
| `SCHEMA_BASE_URI` | both | Yes | Namespace of the loaded schema's SHACL shapes, ending in `/` or `#`, e.g. `https://rosfeatr.eu/rdf/schema/` for `…/schema/PlaceShape`. Must match the schema file and across services. |
| `LONG_TEXT_PREDICATES` | both | No | JSON array of predicate IRIs whose form fields are multi-line text areas. Default: the rfdb schema's description, note and transcription predicates (`rdfs:comment`, `dcterms:description`, `dcterms:abstract`, `skos:definition`, `skos:note`, `skos:scopeNote`, `core:text`). Must match across services. |
| `PROJECT_NAME` | both | No | Name of the knowledge base, shown in the reader's HTML page titles, the editor's header and tab title, and the API docs. Default: `RossijskijFeatrDB`. |
| `EXTERNAL_ID_LINKS` | both | No | JSON object mapping a predicate IRI to a URL template (`$1` is the identifier) for identifiers stored as text; every UI shows them as links. Default: VIAF (`wdt:P214` → `https://viaf.org/en/viaf/$1`) and RISM (`wdt:P5504` → `https://rism.online/$1`). Must match across services. |
| `SEARCH_LABEL_PREDICATES` | both | No | JSON array of predicate IRIs holding a record's other names. The record search behind the editor's link fields matches them besides `rdfs:label` and the IRI, and still lists the record under its label. Only the reader searches, but the field is in the shared base. Default: `skos:altLabel`. Empty: the label and IRI only. |
| `OXIGRAPH_LOAD_TIMEOUT` | both | No | Read timeout (seconds) for bulk Turtle loads. Only the writer loads, but the field is in the shared base. Default: `300`. |
| `SCHEMA_PATH` | both | Yes | Path to `rfdb_schema.ttl` relative to the service working directory. Docker mounts it at `/app/schema/rfdb_schema.ttl`. |
| `CORS_ORIGINS` | both | Yes | JSON array string of allowed CORS origins, e.g. `["http://localhost:5173"]`. Must not contain `"*"` — the app aborts at import time if it does. The reader needs **both** frontend origins. In production it is not set by hand: `docker-compose.prod.yml` builds it from `RFDB_DOMAIN`. |
| `S3_ENDPOINT` | both | No | Object-storage endpoint for digital copies. Empty disables it: `GET /api/v1/dataexplorer/meta/files` then reports `configured: false` instead of erroring. |
| `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | both | No | Object-storage credentials. The writer stages and promotes objects; the reader streams them out. |
| `LOG_FILE`, `LOG_LEVEL`, `TRUNCATE_LOG_ON_STARTUP`, `TRUNCATE_LOG_ON_FRESH_CONTAINER_START` | both | Yes / No | Structured JSON-lines logging. Each service writes to its own volume. `LOG_LEVEL` is one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; default `INFO`. |
| `REPORT_SOURCE_HASH` | both | No | `true` makes `/health` report `source`, a hash of the Python code the service loaded, which the live test suite compares with the checkout. Default `false`; the dev compose file turns it on. Leave it off in production. |
| `VOCAB_PATH` | **curator** | Yes | JSON array of controlled-vocabulary Turtle files (`data/rfdb_vocab.ttl` and the Glottolog languages in this repository). Loaded at startup when `SEED_VOCAB_ON_STARTUP=true`. |
| `DATA_PATH` | **curator** | Yes | Path to the sample-data Turtle file (`data/rfdb_data.ttl` in this repository). Loaded at startup only when `SEED_TEST_DATA_ON_STARTUP=true`. |
| `RESET_DATA_ON_STARTUP` | **curator** | Yes | `true`/`false`. Destructive: wipes every graph in the store before seeding on every startup. Must be `false` in production. |
| `SEED_VOCAB_ON_STARTUP` | **curator** | Yes | `true`/`false`. Load `VOCAB_PATH` on every startup. Should be `true` in all environments. |
| `SEED_TEST_DATA_ON_STARTUP` | **curator** | Yes | `true`/`false`. Load `DATA_PATH` on startup. `true` in dev/test only. |
| `READ_ONLY` | **curator** | No | `true`/`false`. When `true`, rejects `POST /api/v1/curator/entities` and `DELETE /api/v1/curator/entities?id=<iri>` with HTTP 403. Reads are unaffected — they are on the other service. Default: `false`. |
| `READ_ONLY_SHAPES` | **both** | No | JSON array string of shape IRIs to protect from create/update/delete even when `READ_ONLY` is `false`, e.g. `["https://rosfeatr.eu/rdf/schema/LanguageShape"]`. Also drives the `readOnly` flag on both services' shapes route. **Give both backends the same value** — `docker-compose.yml` uses a single YAML anchor for exactly this reason; if they drift, clients get different flags depending on which service they asked. Default: `[]`. |
| `MAX_UPLOAD_MB` | **curator** | No | Per-file ceiling for staged uploads; `0` disables the cap. |

Seeding does not require a running web server: `python curator-backend/scripts/seed.py`
performs the same readiness-poll → optional-reset → seed sequence as the lifespan, so a
deployment can populate the store as a one-shot job.

The S3/object-storage (`S3_*`) and Garage (`GARAGE_RPC_SECRET`) variables consumed by the
compose file are generated into `.env` by `scripts/env-init.sh`; see the
[Quick Start](../README.md#quick-start). Backend log/troubleshooting details are in
[development.md](development.md).

`LOG_FILE` (the JSON-lines log path) is pinned to the `curator_backend_logs` /
`dataexplorer_backend_logs` volume mounts in the compose files. Leave it as set there:
changing it without also updating the volume mount would write logs outside the
persisted volume. The application rotates that file itself (10 MB, five backups).

### Data reset modes

Startup behavior is controlled by `RESET_DATA_ON_STARTUP`.

**Clean slate (empty store)** — erases every graph in the store (`DROP ALL` plus
`CLEAR DEFAULT`), not only `DATA_GRAPH_URI`, before seeding.

```bash
RESET_DATA_ON_STARTUP=true docker compose up -d --build
docker compose up -d   # straight after: recreate the curator without the reset
```

Run the second command as soon as the first is up. In dev the curator restarts on
every code change (`uvicorn --reload`), and each restart runs the startup again: a
container still carrying `RESET_DATA_ON_STARTUP=true` wipes the store on every save.

**Preserve existing data (default)** — keeps existing triples and merges new seed data on top.

```bash
docker compose up --build
```

In both modes, the controlled vocabulary from `data/rfdb_vocab.ttl` is loaded on every startup
(idempotent) when `SEED_VOCAB_ON_STARTUP=true`. Test fixture data from `data/rfdb_data.ttl` is
loaded only when `SEED_TEST_DATA_ON_STARTUP=true` (off by default outside test
environments).

### Data seeding

The application distinguishes between controlled vocabulary and test fixture data. Default
policy: seed vocabulary on, seed test data off, preserve existing data on.

- `data/rfdb_vocab.ttl` is the canonical seed source for controlled vocabulary (core types and role types).
- `dcterms:LinguisticSystem` vocabulary relies on [Glottolog v5.3](https://glottolog.org/meta/downloads) data, imported from `data/glottolog_language.ttl`. Prefix normalization is already applied to the committed file. Only if you re-download a fresh version from glottolog.org, replace `geo1:` with `geo:`:

  ```bash
  sed -i '' 's/geo1:/geo:/g' data/glottolog_language.ttl
  ```

- `data/glottolog_language_dev_subset.ttl` is a cut of `data/glottolog_language.ttl` for dev
  stacks: Italian, English, German and Russian with their families and dialects. The dev
  `.env` written by `scripts/env-init.sh` seeds it through `VOCAB_PATH`; production seeds
  the full file. See [development.md](development.md#running-the-full-application).
- `data/rfdb_data.ttl` is test-only fixture data.

---

## Deploy modes — read vs. full

Which *kind* of instance this is, is a deploy-time choice with no code change and no
separate compose file. That is possible because the reader/writer boundary is a **service**
boundary: `dataexplorer-backend` owns every read plus the whole `/rdf/` data space and
never depends on the write tier, so the write tier can simply be absent.

| Mode | Command | Services |
|---|---|---|
| **read** — public browse/explore | `docker compose up` *(no flag)* | oxigraph, garage, dataexplorer-backend, graphexplorer-frontend *(+ proxy in prod)* |
| **full** — curate + browse | `docker compose --profile full up` | the above **plus** curator-backend, curator-frontend |

Base services carry **no `profiles:` key**, so read mode needs no flag; only `curator-*`
are gated. Set `COMPOSE_PROFILES=full` in `.env` / `.env.prod` to make full mode the
default for an instance.

In read mode a public deployment omits the **entire `/api/v1/curator/*` namespace** rather
than deploying a service that refuses each route with a 403. That works because every
prefix is named after the service that owns it ([API reference](architecture.md#api-reference)) — see
[the proxy section](#the-proxy) for what the edge does with those paths when nothing is
behind them.

**Read mode is orthogonal to `READ_ONLY`.** The mode decides whether a writer is deployed
at all; `READ_ONLY=true` freezes a writer that *is* deployed. `full` + `READ_ONLY=true` is
a valid frozen-editor demo.

### The dependency invariant

Every `depends_on` target must be a **base** service, so no dependency ever points into a
profile its own service does not activate. Compose versions disagree about what happens
then — some silently activate the dependency's profile (so read mode quietly starts the
editor), some hard-error (so read mode will not start at all). Either way the failure shows
up at deploy time on someone else's machine.

`curator-frontend` → `curator-backend` is fine, because both are `full`. Nothing base may
depend on anything `full`. This is checked automatically by
`tests/core/test_compose_topology.py`, which reads both compose files, so an edit that
breaks it fails the ordinary test gate rather than a deploy.

### Populating a read-only instance

A read instance serves data it did not create, across **two** stores: triples in Oxigraph
and digital-copy blobs in Garage. Bringing up a fresh read host therefore has a
prerequisite and a populate step, in this order:

0. **Bootstrap Garage** (once per fresh `garage_*` volume set, in *every* mode). A fresh
   volume has no layout, bucket or key, and none can be declared in `garage.toml`:

   ```bash
   scripts/garage-init.sh                                                    # dev
   COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.prod scripts/garage-init.sh   # prod
   ```

   Without it, `GET /rdf/data/{id}/content` fails with a `StorageNotInitialized` 503 whose
   operator hint points at this script.

Then populate both stores one of three ways:

- **(a) Snapshot import.** The only path that gives a fresh host the curated triples
  **and** their attached blobs in one consistent step. **Not built yet** — it is the
  import/restore counterpart of the snapshot-export item in [TODO.md](../TODO.md), and it
  is what a standalone read deploy on a brand-new host is waiting on.
- **(b) Carried-over volumes.** The `oxigraph_data` + `garage_*` volumes persist from a
  prior `full` run on the same host, so a read bring-up reuses them as-is. No import
  needed. **This path works today.**
- **(c) External SPARQL endpoint (triples only).** The reader uses only the `TripleStore`
  read subset, so `OXIGRAPH_URL` can point at an already-populated remote store and
  `oxigraph` can be dropped. Garage still needs its blobs by (a) or (b), or digital-copy
  downloads 404.

A vocabulary-only bare demo host can also be seeded directly, without a running writer:

```bash
docker compose run --rm curator-backend python scripts/seed.py    # needs --profile full
```

That loads vocab/fixtures only — never curated data or blobs. (It is
`python scripts/seed.py`, not a bare `seed`: the image has no entrypoint dispatcher.)

---

## Production Deployment

> **Status: complete, not yet deployed.** The whole topology runs locally: both frontend
> production images build, and `proxy/Caddyfile` serves each route in [Verify](#5-verify)
> against live backends. There is no host, domain, certificate or data yet, so treat the
> procedure below as tested in parts, and expect the first real deployment to surface
> something about DNS, TLS or file ownership that a local run cannot.

### Target topology

A single Docker host running `docker-compose.prod.yml`. Seven services in full mode, five
in read mode:

| Service | Network | Ports | Notes |
|---|---|---|---|
| `proxy` (Caddy) | `edge` | **80, 443** | The only host-facing service. Terminates TLS, routes by path prefix. |
| `curator-frontend` | `edge` | — | Static build of the editor, served by `caddy:2.11-alpine`. `full` only. |
| `graphexplorer-frontend` | `edge` | — | Static build of the explorer, same. Base. |
| `curator-backend` | `internal` + `edge` | — | The only writer. `full` only. |
| `dataexplorer-backend` | `internal` + `edge` | — | All reads plus the `/rdf/` data space. Base. |
| `oxigraph` | `internal` | — | Unauthenticated; must never be reachable from outside. |
| `garage` | `internal` | — | Unauthenticated at the S3 layer; blobs leave only through the reader. |

- Neither store is on `edge` at all, so nothing in the web tier can reach them and a
  browser cannot reach them under any circumstances.
- Automatic HTTPS via Caddy/ACME (Let's Encrypt), so `RFDB_DOMAIN`'s DNS must resolve to the
  host's public IP **before** the proxy starts.
- Stateful volumes: `oxigraph_data` (triples), `garage_data` + `garage_meta` (digital-copy
  bytes and the cluster's bucket/key metadata), `caddy_data` (certificates). Back up the
  first three **together** — a restore of triples without blobs leaves records pointing at
  files that do not exist.

### The proxy

`proxy/Caddyfile` routes the whole surface by path prefix, with no method awareness:

| Prefix | Upstream |
|---|---|
| `/api/v1/curator/*` | `curator-backend:8000` (plus the body cap) |
| `/api/v1/dataexplorer/*` | `dataexplorer-backend:8001` |
| `/rdf/*` | `dataexplorer-backend:8001` |
| `/explorer/*` | `graphexplorer-frontend:80` (prefix stripped) |
| everything else | `$RFDB_ROOT_UPSTREAM`, default `curator-frontend:80` |

It can be a prefix table because every prefix names the service that owns it
([API reference](architecture.md#api-reference)): no path is served by two services, so
nothing needs splitting by method.

Three details that are load-bearing rather than stylistic:

- **`/rdf/*` must be served by this domain and no other.** Those URLs are the permanent
  public identifiers stored inside the triples. A second origin serving them would give
  every resource two URLs, which is what a stable identifier is not. Keep `RFDB_DOMAIN`
  equal to the host of `DATA_BASE_URI` and `SCHEMA_BASE_URI` in `.env.prod`.
- **The explorer's image must be built with `VITE_BASE_PATH=/explorer/`** to match the route
  that serves it. Vite bakes asset URLs against `base` at build time, so a bundle built for
  `/` would request `/assets/…`, hit the fallback route, and be handed the *editor's*
  `index.html` — a blank page rather than an honest 404.
- **Unmatched `/api/**` and `/health` return an explicit 404.** Without those two blocks the
  SPA fallback answers them with `200` and an HTML page, because that is what makes
  client-side routing work. A `/health` that returns `200` with both backends down is worse
  than no `/health` at all, and it is why health is deliberately not proxied — see the note
  at the foot of the Caddyfile.

The **body cap is enforced at the edge**, not only by `MAX_UPLOAD_MB`. The app-level cap does
return 413, but Starlette spools the entire multipart body to a temp file *before* the
handler runs, so it protects the Garage volume while the writer's disk absorbs the whole
upload first. `.env.prod` derives the proxy's `request_body max_size` from the same variable,
so the two cannot drift.

### 1. Host prerequisites

Docker Engine + Compose v2 (`docker compose version` shows v2.x). Firewall: allow inbound
80/443 only, and explicitly close 7878/8000/8001/5173 if a prior dev deployment opened
them. (Dev's `graphexplorer-frontend` also publishes on 80, same as prod's `proxy` — that
one can't be told apart at the firewall, so make sure no dev stack is still running before
this host goes live.) DNS for the domain must already point here.

### 2. Get the files onto the host

Clone the repo, then `chmod o+r -R schema data` — each backend runs as uid 1001 and mounts
those read-only. Decide whether to reuse or discard any `oxigraph_data` volume from prior
dev runs.

### 3. Configure environment

```bash
cp .env.prod.example .env.prod && chmod 600 .env.prod
```

Fill in real values. Every `${VAR:?…}` in the compose file must be set or the stack refuses
to start — that is deliberate, so a missing value fails immediately instead of deploying a
dev default. `.env.prod.example` documents each one, including which variables are
intentionally *not* env knobs (`READ_ONLY_SHAPES` is one YAML anchor per compose file, so
both backends serve the same `readOnly` flags; `S3_ENDPOINT`/`S3_REGION` are fixed by the
topology).

Set `COMPOSE_PROFILES` to pick the [mode](#deploy-modes--read-vs-full) for this instance.

### 4. Bootstrap and start

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
COMPOSE_FILE=docker-compose.prod.yml ENV_FILE=.env.prod scripts/garage-init.sh   # ONCE per fresh garage volume
```

The bootstrap is the same script dev uses, parametrized rather than forked — the four steps
(layout → bucket → key import → grant) are identical, and two copies would drift exactly
where a mistake is least recoverable, since a layout version cannot be re-applied. It is
idempotent, and needed again only after `down -v`.

A read-mode host also needs a populate step; see
[Populating a read-only instance](#populating-a-read-only-instance).

### 5. Verify

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod ps    # every service healthy
D=https://<domain>
curl -sI  $D/                                        # 200, the app
curl -s   $D/api/v1/dataexplorer/meta/prefixes       # 200 JSON, the read tier
curl -sI  $D/explorer/                               # 200, the explorer
curl -s   $D/api/v1/curator/shapes | head -c 80      # 200 JSON, the write tier (full mode)
curl -sI  -H 'Accept: text/turtle' $D/rdf/data/<id>  # 200 text/turtle, the data space
curl -sI  $D/health                                  # 404 — not exposed, by design
```

Health is checked with `ps` rather than over HTTP: the containers run their own
healthchecks, and the curator's `/health` payload includes a seed report, which is operator
information. Then confirm from **outside** the host that 7878/8000/8001/5173 are
unreachable.

### Ongoing operations (once live)

- **Redeploy after code changes**: `git pull`, then repeat [step 4](#4-bootstrap-and-start)
  without the bootstrap — `--build` rebuilds only changed images.
- **A frontend change needs `--build`, not `restart`.** Vite inlines every `VITE_*` value
  into the JS bundle at build time; once the image is static files there is no server reading
  env per request. The same goes for changing `RFDB_DOMAIN`, since the editor's "Open in
  Explorer" link is derived from it at build time.
- **A `proxy/Caddyfile` change** needs only
  `docker compose -f docker-compose.prod.yml --env-file .env.prod restart proxy`. Prefer that
  over `caddy reload` inside the container: reload re-adapts the file, and an env var that is
  unset at that moment becomes an *empty token* rather than an error — a `reverse_proxy` with
  no upstream, which serves empty `200`s and logs nothing. The Caddyfile carries an in-file
  default for exactly this reason.
- **Back up the three data volumes together**, on a schedule matching your data-loss
  tolerance. Separately is worse than useless: triples restored without their blobs leave
  records pointing at files that do not exist.

  Each volume is named `<COMPOSE_PROJECT_NAME>_<volume>`, and the project name is in
  `.env.prod` (`rfdb-prod` unless changed), so the loop reads it from there:

  ```bash
  PROJECT=$(grep '^COMPOSE_PROJECT_NAME=' .env.prod | cut -d= -f2)
  for v in oxigraph_data garage_data garage_meta; do
    docker run --rm -v "${PROJECT}_$v:/data" -v "$(pwd):/backup" \
      alpine tar czf "/backup/${PROJECT}_${v}_$(date +%F).tar.gz" -C /data .
  done
  ```

  `caddy_data` holds the certificates and ACME account key. Losing it means re-issuing, which
  Let's Encrypt rate-limits — back it up too, or accept the wait.
- **`docker compose down -v` destroys every one of those volumes.** Use `docker compose down`
  without `-v` for routine stop/start, and remember that `-v` also means re-running
  `garage-init.sh`.
