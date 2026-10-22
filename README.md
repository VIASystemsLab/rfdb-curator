# RFDB Curator

Standalone SHACL-driven curation application.
Forms are generated dynamically and automatically from the active SHACL schema, so **the schema is the source of truth** for record types, fields, constraints, datatypes, and relations. 
Swap the schema and the whole editor follows — see [The Schema](#the-schema).
The web application provides shape-aware CRUD, validation, autocomplete, and record inspection for RDF instance data.

The current use case is the curation of `RossijskijFeatrDB`, developed within the [Rossiysky Θeatr: Music Sources of the Russian Empire](https://rosfeatr.eu/) project.
The current schema features record types for musical works, expressions, manifestations, sources/items, digital copies, persons, roles, agent-role assignments, places, subjects, source types, holding organizations, performances, and controlled-vocabulary languages.
[SHACL shapes](https://www.w3.org/TR/shacl12-core/) are aligned with the [Polifonia Core Ontology](https://github.com/polifonia-project/core-ontology) and [LRMoo](https://cidoc-crm.org/lrmoo/) to support the FRBR-based work–expression–manifestation–item (WEMI) hierarchy.


This repository is self-contained: both backend services, both frontends, the shared library, schema, data, and the Docker Compose runtime are all maintained at the repository root.
The API is split by responsibility — `curator-backend` is the only service that writes to the store, `dataexplorer-backend` answers every read — so a read-only deployment can run without the writer at all.
For deeper topic guides, see the [documentation](#documentation).


---

## Core Features

- Dynamic form generation from SHACL `sh:NodeShape` definitions
- Shape-aware create, read, update, and delete operations
- RDF instance data stored in [Oxigraph](https://github.com/oxigraph/oxigraph)
- SHACL validation with [pySHACL](https://github.com/RDFLib/pySHACL)
- Autocomplete for linked RDF resources
- Record inspection through RDF triples
- Controlled-vocabulary seeding from Turtle files
- Digital-copy uploads (e.g. PDF scans) held in S3-compatible object storage, with RDF as the source of truth
- Docker Compose deployment

## Tech Stack

- **Backend:** [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://uvicorn.dev/)
- **Frontend:** [React](https://react.dev/) + [Vite](https://vite.dev/)
- **RDF Store:** [Oxigraph](https://github.com/oxigraph/oxigraph), using [SPARQL](https://www.w3.org/TR/sparql11-query/) and [Graph Store Protocol](https://www.w3.org/TR/sparql11-http-rdf-update/)
- **Object Storage:** [Garage](https://garagehq.deuxfleurs.fr/) (S3-compatible), via [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) — holds digital-copy files
- **Validation:** [pySHACL](https://github.com/RDFLib/pySHACL)
- **RDF/Data Model:** [rdflib](https://rdflib.readthedocs.io/), [Turtle](https://www.w3.org/TR/turtle/), [JSON-LD](https://www.w3.org/TR/json-ld11/)
- **Runtime:** [Docker Compose](https://docs.docker.com/compose/)

---

## Quick Start

### Prerequisites

- Docker
- Docker Compose
- OpenSSL (for the one-time secret generation step below)

### First-time setup

The stack needs a repo-root `.env` (Garage RPC secret + the S3 credentials shared by Garage and both backends) and a one-time Garage bootstrap (cluster layout, bucket, access key). 
Both are handled by helper scripts. `.env` is gitignored, so a fresh checkout has none.
Compose fails at startup if `GARAGE_RPC_SECRET` is missing, while uploads fail until Garage is bootstrapped.

From the repository root:

```bash
# 1. Generate .env with fresh dev secrets (refuses to clobber an existing .env)
scripts/env-init.sh

# 2. Build and start the stack
docker compose up -d --build

# 3. Bootstrap Garage — run ONCE, after the first `up` (and again after any `down -v`)
scripts/garage-init.sh
```

Both scripts are idempotent and documented in their file headers.

### Run

Once set up, ordinary runs need only Compose:

```bash
# First run of a session or after Dockerfile changes
docker compose up --build

# Subsequent runs
docker compose up
```

**Which services start is a mode**, not a fixed list. Base services (Oxigraph, Garage, the
read backend, the graph explorer) run in every mode, and the two curator services are gated
behind the `full` profile — so a bare `docker compose up` is the read-only stack a public
instance would run. `scripts/env-init.sh` writes `COMPOSE_PROFILES=full` into `.env`, so
development gets the whole six-service stack by default.

*On an existing clone,* `env-init.sh` will not overwrite your `.env` — add
`COMPOSE_PROFILES=full` to it yourself, or pass `--profile full` per invocation. Without it
the editor on `:5173` simply will not start. See
[docs/deployment.md](docs/deployment.md#deploy-modes--read-vs-full).

Stop services:

```bash
docker compose down
```

Remove volumes and clear all data (destructive — wipes Oxigraph triples **and** Garage layout/bucket/key). After this you must re-run `scripts/garage-init.sh`:

```bash
docker compose down -v
```

### Service URLs

| Service | URL | Role |
|---|---|---|
| curator-frontend | http://localhost:5173 | Editor UI |
| graphexplorer-frontend | http://localhost | Read-only graph visualizer |
| curator-backend | http://localhost:8000 | **Writes** — create/update/delete, validation, upload staging |
| dataexplorer-backend | http://localhost:8001 | **Reads** — listing, search, graph traversal, metadata, downloads |
| Oxigraph | http://localhost:7878 | RDF triple store |
| Garage (S3 API) | http://localhost:3900 | Object storage for source PDFs |

The API is split by responsibility: `curator-backend` is the only service that
mutates the store. `dataexplorer-backend` answers every read and can run without
the writer at all.

A third user-facing surface lives inside `dataexplorer-backend` rather than as its own
service: visiting an entity's IRI in a browser — e.g.
<http://localhost:8001/rdf/data/{local_name}> — renders an HTML description page (labels,
properties, outbound/inbound links). The same URL serves Turtle, JSON-LD, RDF/XML or
N-Triples to a non-browser client via content negotiation; see [API Reference](#api-reference).

Startup seeding, data-reset modes, and the full environment-variable reference live in the [development & testing deployment guide](docs/deployment.md#development--testing-deployment).

---

## The Schema

The loaded SHACL schema is the source of truth: every record type, form field, constraint, datatype, and relation is derived from it at runtime. This repository ships the rfdb schema in `schema/schema.ttl`, copied from a tagged release of [rfdb-schema](https://github.com/VIASystemsLab/rfdb-schema) and recorded in [schema/README.md](schema/README.md); model changes go upstream. **To run a different model, point `SCHEMA_PATH` at it**: forms, validation, and the record-type list all follow, with no code changes. What a schema must provide is in [docs/data-model.md](docs/data-model.md); what a fork sets is in [docs/custom-schema.md](docs/custom-schema.md).

### Data Model

The active model uses [LRMoo](https://cidoc-crm.org/lrmoo/) (rather than the older FRBR/FaBiO model) and draws on [LRMoo](https://cidoc-crm.org/lrmoo/), [CIDOC CRM](https://cidoc-crm.org/), the Polifonia [Core](https://github.com/polifonia-project/core-ontology) / [Music Meta](https://github.com/polifonia-project/music-meta-ontology) / [Source](https://github.com/polifonia-project/source-ontology) ontologies, [Dublin Core Terms](https://www.dublincore.org/specifications/dublin-core/dcmi-terms/), [PRISM](https://www.w3.org/submissions/prism/), [SKOS](https://www.w3.org/TR/skos-reference/), [FOAF](http://xmlns.com/foaf/spec/), [Schema.org](https://schema.org/), [Wikidata](https://www.wikidata.org/) direct properties, and RDF/RDFS/OWL/XSD.

LRMoo hierarchy: a **Musical Work** is the abstract work; an **Expression** is an intellectual realization (e.g. a libretto); a **Manifestation** is an edition or product type; a **Source / Item** is a specific physical or documentary copy. Each level refines the one above it, from Work through Expression and Manifestation to Source/Item, and every WEMI link points from the more concrete record up to its parent — so create parents before children.

### Main SHACL Shapes

The current schema includes these primary record types:

- `rfdbs:MusicalWorkShape`: musical work, targeting `mm:MusicEntity`, constrained as `lrmoo:F1_Work`
- `rfdbs:ExpressionShape`: expression, targeting `lrmoo:F2_Expression`
- `rfdbs:ManifestationShape`: manifestation, targeting `lrmoo:F3_Manifestation`
- `rfdbs:SourceShape`: source/item, targeting `source:Source` and `lrmoo:F5_Item`
- `rfdbs:DigitalCopyShape`: digital copy (PDF scan) of a source, targeting `schema:DigitalDocument` (helper shape, managed via the file-upload panel)
- `rfdbs:PersonShape`: person, targeting `core:Person`
- `rfdbs:RoleShape`: role, targeting `core:Role`
- `rfdbs:AgentRoleShape`: agent-role assignment, targeting `core:AgentRole`
- `rfdbs:PlaceShape`: place, targeting `core:Place`
- `rfdbs:SubjectShape`: subject, targeting `cidoc:E89_Propositional_Object`
- `rfdbs:SourceTypeShape`: source type, targeting `core:Type`
- `rfdbs:HoldingOrganizationShape`: holding organization, targeting `core:Organization`
- `rfdbs:ContributorShape`: donor/contributor record for digital-copy provenance
- `rfdbs:PerformanceShape`: staged performance, targeting `lrmoo:F31_Performance`
- `rfdbs:LanguageShape`: controlled-vocabulary language record, targeting `dcterms:LinguisticSystem` (seeded from Glottolog, see [Data Seeding](docs/deployment.md#data-seeding))

### Form Generation

Each `sh:NodeShape` becomes a form type and each `sh:property` becomes a form field. The generator derives required fields from `sh:minCount`, cardinality from `sh:maxCount`, datatypes from `sh:datatype`, IRI-valued fields from `sh:nodeKind`, linked target classes from `sh:class`, nested forms from `sh:node`, alternatives from `sh:or`, fixed values from `sh:hasValue`, help text from `sh:description`, and closed-shape behavior from `sh:closed`.

Shapes with a `sh:property` on `rdfs:label` are treated as standalone entities; shapes without one are helper/bridge nodes rendered inline when referenced by a parent (e.g. `rfdbs:AgentRoleShape`). Shapes whose shape-level `sh:or` branches into multiple `sh:class` alternatives (e.g. `rfdbs:ContributorShape`, `foaf:Person` or `foaf:Organization`) surface a type-selection dropdown at creation time.

> What the application requires of any schema (entity and helper shapes, the constraints the editor understands, identifiers, uploads) is in [docs/data-model.md](docs/data-model.md). The rfdb model's own decisions and rationale are in upstream's [data-model.md](https://github.com/VIASystemsLab/rfdb-schema/blob/v2.2.2/docs/data-model.md). Per-shape fields and cardinalities live in the schema itself (`schema/schema.ttl`, or `GET /api/v1/dataexplorer/shapes`). The schema-driven extraction and validation pipeline is described in [docs/architecture.md](docs/architecture.md).

---

## Configuration

All settings are loaded from environment variables; each service fails fast if a required variable is missing or malformed. The source of truth is the `environment:` blocks in `docker-compose.yml` plus the settings models.

Both services share a base (`rfdb-core/rfdb_core/config.py`: store connection, schema path, CORS, logging, S3). `curator-backend/core/config.py` extends it with the write-side surface — seeding, data reset, `READ_ONLY`, `READ_ONLY_SHAPES`, `MAX_UPLOAD_MB`. `dataexplorer-backend` adds nothing: everything a reader needs is already in the base. Because the base ignores unknown variables, one shared `.env` across both services is safe — the writer-only values are simply invisible to the reader.

The full environment-variable reference, data-reset modes, and seed sources (controlled vocabulary + Glottolog languages) are documented in the [development & testing deployment guide](docs/deployment.md#development--testing-deployment). The production stack is **complete but has never been deployed** — every piece exists and the whole topology has been exercised locally, but there is no host, domain or certificate yet. Runbook and the one remaining gap: [docs/deployment.md](docs/deployment.md#production-deployment).

---

## API Reference

Both services are FastAPI, so the complete, always-current reference is generated from the running apps. With the stack up, open **<http://localhost:8000/docs>** (writes) and **<http://localhost:8001/docs>** (reads) for interactive Swagger UI; raw schemas at `/openapi.json` on each.

The core of the schema-driven pipeline is a handful of endpoints, split by which service owns them:

| Method | Path | Service | Description |
|---|---|---|---|
| `GET` | `/api/v1/{service}/shapes` | both | Available SHACL NodeShapes with metadata, field descriptors and `readOnly` flags |
| `GET` | `/api/v1/curator/forms?shapeId=...` | curator | Generated form schema for one shape |
| `POST` | `/api/v1/curator/entities` | curator | Create or update an entity (JSON-LD → SHACL validate → Turtle load) |
| `POST` | `/api/v1/curator/validate` | curator | Dry-run SHACL validation without persisting |
| `GET` | `/api/v1/dataexplorer/entities/get?id=<iri>` | dataexplorer | All triples for one entity, in the editor's JSON shape |
| `GET` | `/rdf/data/{id}` | dataexplorer | The same entity, content-negotiated: **HTML** for a browser, or Turtle / JSON-LD / RDF-XML / N-Triples otherwise |

Two URL spaces, deliberately: `/rdf/…` holds the **data** — public, permanent, unversioned identifiers that are stored inside triples and can never move — while `/api/v1/{service}/…` is this project's own operational surface, named after whichever service owns it so no path ever means two things.

The shapes route is served by both services and returns the **same payload from one implementation** (`rfdb_core.shapes`), `readOnly` flags included. Both must therefore be given the same `READ_ONLY_SHAPES`; `docker-compose.yml` does that with a single YAML anchor. The upshot is that the editor can start, browse and search with the write tier stopped.

The full endpoint table (data listing, autocomplete, file staging, downloads, and the metadata API) with per-service ownership is in [docs/architecture.md](docs/architecture.md#api-reference).

---

## Documentation

The root README is the entry point (overview, setup, schema, API, configuration). These topic guides go deeper; where any document and the implementation diverge, the implementation and the active `schema/schema.ttl` take precedence.

| Document | Covers |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | What the editor is for, the WEMI data model in brief, and how to run it locally and in production. |
| [docs/data-model.md](docs/data-model.md) | The data-model contract: what any schema must provide for this application, and the rfdb schema this repository runs. Per-shape fields live in `schema/schema.ttl`. |
| [docs/architecture.md](docs/architecture.md) | System design: the schema-driven pipeline, the writer/reader service split and what each owns, the API endpoint reference, SHACL extraction, validation and delete behavior, the metadata API, and the storage stack. |
| [docs/development.md](docs/development.md) | Development workflow: environment setup, code quality, CI, schema and data change workflows, troubleshooting, and the commit checklist. |
| [docs/deployment.md](docs/deployment.md) | Deployment & operations: the development/testing configuration, data-reset modes, and seed sources; the read-vs-full deploy modes; plus the production runbook and its complete-but-never-deployed status. |
| [docs/custom-schema.md](docs/custom-schema.md) | Running your own deployment: the checklist of what a fork sets (schema, base IRIs, graph, seed data, long-text fields, Welcome guide) and where. |

The live task list lives in the root `TODO.md`.

---

## Repository Structure

```text
rfdb-curator/
├── pyproject.toml                # uv workspace root (members + the one ruff config)
│
├── rfdb-core/                    # Shared library — imported by both services
│   └── rfdb_core/
│       ├── triplestore/          # The store seam: TripleStore protocol + OxigraphStore
│       ├── schema_extractor.py   # SHACL → form/shape metadata
│       ├── file_storage.py       # Object-storage seam (S3/Garage)
│       ├── files_state.py        # Digital-copy state snapshot (RDF vs. storage)
│       ├── app_factory.py        # Shared CORS / access log / storage-error wiring
│       ├── config.py             # BaseServiceSettings — the fields both services read
│       ├── prefixes.py           # Curated CURIE map
│       ├── iri.py                # IRI guard for SPARQL interpolation
│       ├── models_data.py        # Record-list response models
│       └── vocab.py              # Digital-copy vocabulary terms
│
├── curator-backend/              # WRITES ONLY — :8000
│   ├── api/                      # data (POST/DELETE), files (staging), shapes, validate
│   ├── core/                     # config, SHACL validator, validation merge, seeder,
│   │                             #   blank-node handler
│   ├── models/                   # Pydantic write-path schemas
│   ├── scripts/                  # seed.py, cleanup_files.py, check_prefixes.py
│   ├── app.py                    # FastAPI app + lifespan (seeds on startup)
│   ├── Dockerfile
│   └── pyproject.toml
│
├── dataexplorer-backend/         # READS ONLY — :8001
│   ├── api/                      # data (GET), entities, graph, meta, files, shapes
│   ├── core/config.py            # Plain BaseServiceSettings — no writer fields
│   ├── app.py                    # FastAPI app; no validator, no seeder, no reset
│   ├── Dockerfile
│   └── pyproject.toml
│
├── curator-frontend/
│   ├── src/                      # React components, API client, JSON-LD/prefix utils
│   ├── Dockerfile
│   ├── vite.config.js            # Dev server + proxy: /api/v1/curator → :8000,
│   └── package.json               #   /api/v1/dataexplorer + /rdf → :8001
│
├── graphexplorer-frontend/       # Standalone read-only graph visualizer (own Vite app;
│                                 #   proxies /api + /rdf → dataexplorer-backend:8001)
│
├── schema/                       # rfdb SHACL schema, copied from upstream (see schema/README.md)
├── data/                         # vocab.ttl (controlled vocabulary) + data.ttl (test fixtures)
├── docs/                         # Topic documentation (see the Documentation section)
├── docker-compose.yml
├── garage.toml                   # Object-storage (Garage) configuration
├── scripts/                      # Host-side helpers (garage-init.sh, env-init.sh)
├── AGENTS.md / .agent-defs/      # Agent instructions
├── tests/                        # One subdirectory per Python member, one pytest run each
│   ├── core/                     #   rfdb-core: store seam, schema, prefixes
│   ├── curator/                  #   writes, validation, seeding, upload staging
│   └── dataexplorer/             #   reads, graph, meta, downloads, service contract
├── TODO.md
└── README.md
```

---

## Copyright & License

Copyright © 2026 University of Verona — Digital Humanities.

Developed within the [Rossiysky Θeatr: Music Sources of the Russian Empire](https://rosfeatr.eu/) project.

This program is free software: you can redistribute it and/or modify it under the terms of the [GNU General Public License v3.0](LICENSE) as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [LICENSE](LICENSE) file for the full text.
