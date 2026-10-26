# Overview

## Project Purpose

RossijskijFeatrDB (rfdb-curator) is a standalone SHACL-driven RDF curation project focused on Russian theatrical works and libretti. The repository ships a full editing stack for schema-aware CRUD operations, validation, and data inspection.

## Users

- Musicologists and theatre historians curating records
- Data curators managing linked RDF entities
- Developers maintaining the backend services, frontends, and schema behavior

## Main Features

1. **SHACL schema** (loaded from `SCHEMA_PATH`) drives forms and validation. This repository ships the rfdb schema in `schema/`, copied from a tagged upstream release (`schema/README.md`).
2. **Shared library** (`rfdb-core/`) holds what both services need: the `TripleStore` seam, the SHACL schema extractor, the object-storage seam, the CURIE map, the settings base, and the shared FastAPI edge wiring. Framework-free apart from one module behind a `web` extra.
3. **Curator backend** (`curator-backend/`, :8000) is the **only** service that writes: create/update, delete, SHACL validation, upload staging, and startup seeding.
4. **Data Explorer backend** (`dataexplorer-backend/`, :8001) answers every read: listing, counts, entity fetch, autocomplete, graph traversal, metadata, digital-copy downloads. It has no validator, no seeder and no write route, and runs independently of the curator.
5. **Frontend editor** (`curator-frontend/`) renders dynamic forms and record workflows.
6. **Graph Explorer** (`graphexplorer-frontend/`) is a read-only, schema-driven visualizer of entity lineage & relationships — a standalone Vite app that talks only to `/api` (`GET /api/v1/dataexplorer/graph/node`).
7. **RDF data** (`data/rfdb_vocab.ttl`, `data/rfdb_data.ttl`) seeds vocabulary and optional fixtures.
8. **Validation pipeline** (pySHACL + merged validation graph) enforces schema constraints — curator only, since validation gates writes.
9. **Object storage** (Garage, S3-compatible) holds source digital copies (PDF scans); metadata lives in RDF, bytes in Garage. Note the split: a copy is *uploaded* through the curator and *downloaded* through the reader — but only once **published**, meaning a parent entity references it in RDF. Until then it is curator working state, previewable only via the curator's `GET /api/v1/curator/files/staged/{fileId}`; the reader 404s it. Publication is always an RDF question, never "which prefix holds the bytes".
10. **Docker runtime** (`docker-compose.yml`) runs both frontends, both backends, Oxigraph, and Garage together.

## Business Goals

- Keep RFDB curation schema-driven and adaptable to model evolution.
- Maintain data quality through SHACL validation before persistence.
- Provide a practical editorial interface over standards-compliant RDF.

## Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, uvicorn, pydantic-settings, rdflib, pyshacl |
| Frontend | React, Vite |
| RDF store | Oxigraph |
| Object storage | Garage (S3-compatible), boto3 client |
| Runtime | Docker Compose |
| Testing | pytest |
| CI/CD | GitHub Actions |

Per component, development against production, and where each version is pinned: [docs/architecture.md](../docs/architecture.md#software-stack), "Software Stack".
