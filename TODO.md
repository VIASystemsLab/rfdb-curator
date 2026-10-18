# TODO — RossijskijFeatrDB

Open work grouped by area — **UI**, **Backend**, **DevOps** — with priority and
`REQUIRES BRAINSTORM` tags kept inline. Completed work is dropped once nothing open depends
on it; where an open task builds on something already shipped, that context is noted inline.
[Non-Goals](#non-goals) closes the file.

---

## UI

- [ ] **NEW FEATURE — REQUIRES BRAINSTORM — exploration view of connected sub-editions.**
  Given a starting entity (MusicalWork, Expression, …), show everything derived from it down the
  chain — its Expressions, their Manifestations, the Sources/Items — as one navigable view rather
  than node-by-node expansion in the graph explorer.
  **Approach — SPARQL-driven templates.** Each view is a template that pairs a layout with one or
  more SPARQL queries; the template is instantiated with the starting entity's IRI, the queries
  run against the read path (`dataexplorer-backend`), and the layout is hydrated with the result
  bindings. New views are then added by writing a query + template, not new frontend code.
  Open questions:
  - which entity types can be the starting point, and one template per type or a shared one;
  - which relations count as "sub-edition" (e.g. `lrmoo:R4_embodies`, `R76_is_derivative_of`) —
    encoded in the queries, so changeable without code;
  - template format and where templates live (files in the repo vs. RDF in the schema graph);
  - safety: only parameterised, read-only queries (SELECT/CONSTRUCT) with the IRI bound, never
    user-supplied SPARQL; result-size limits for large subtrees;
  - layout: tree vs. timeline; whether it lives in the graph explorer or in the curator.
- [ ] Auto-refresh entity lists (e.g. "has place" relations) when backend data changes behind the scenes.
- [ ] Records pagination with a default page size of 20 (frontend side; pairs with cursor-based SPARQL pagination under [Backend](#backend)).
- [ ] Real-time validation — debounced SHACL checking on blur/change via `POST /api/v1/curator/validate`.
- [ ] Render external IDs as links in every UI. The schema stores VIAF (`wdt:P214`, since rfdb-schema 2.0.0) and RISM (`wdt:P5504`, since 2.2.0) IDs as bare text, e.g. `"100995617"` and `"people/1874"`, and leaves building links to the application. Use Wikidata's formatter URLs (P1630), as listed in the schema's [external identifiers](https://github.com/VIASystemsLab/rfdb-schema/blob/v2.2.2/docs/external-identifiers.md) doc: VIAF `https://viaf.org/en/viaf/$1`, RISM `https://rism.online/$1`. Keep the predicate → template map in one place, so a schema change is one edit. Places: the curator form (show the link next to the text input), the graph explorer Inspector (these IDs are literals, not `externalLinks`), and the dataexplorer resource HTML page. Update the comments that still call P214 an IRI input: [FormField.jsx](curator-frontend/src/components/FormField.jsx) and `relation_predicates` in [graph.py](dataexplorer-backend/api/graph.py).
- [ ] UI language switch (English / Italian). The schema names shapes and fields in both languages (`sh:name`), and vocabulary concepts carry an Italian `rdfs:label` with an English `skos:altLabel`, but the backend picks English for every label (`_english_first` in [schema_extractor.py](rfdb-core/rfdb_core/schema_extractor.py), `preferred_label` in [graph.py](dataexplorer-backend/api/graph.py)). Let the user pick the language in each UI, defaulting to the browser's: the shapes and entity endpoints return every language (or take a `lang` parameter), and the frontends choose. Cover shape and field names, controlled-vocabulary labels, and entity labels.
- [ ] Warn on duplicate `owl:sameAs`: when a same-as value is entered, check whether another record already has it and warn the user (needs a backend lookup).
- [ ] Data Context Panel enhancements on top of the shipped read-only baseline (the panel plus its metadata API — `GET /api/v1/dataexplorer/meta/prefixes`, `GET /api/v1/dataexplorer/meta/graphs` with per-graph active/empty status, `GET /api/v1/dataexplorer/meta/files`):
  - [ ] Prefixes: per-entry `source` attribution (`schema` / `jsonld-context` / `runtime`); explicit prefix-drift warnings when mappings differ across schema, JSON-LD context, and runtime; copy Turtle prefix declaration / copy namespace IRI; search by prefix or namespace substring.
  - [ ] Phase 2 — operational guardrails: store health indicators, metadata freshness timestamp, schema/context mismatch diagnostics, actionable hints.
  - [ ] Phase 3 (optional, gated) — advanced operations: graph snapshot export, non-destructive graph diagnostics, controlled operational utilities. Keep the panel read-only in the baseline deployment; do not add delete/clear actions unless separately designed and approved.

---

## Backend

### High priority

- [ ] **REQUIRES BRAINSTORM + USER APPROVAL — optimize startup bulk load of large vocab files.**
  The ~38 MB `data/glottolog_language.ttl` is re-loaded via `POST /store` on every startup
  ([curator-backend/core/seeder.py](curator-backend/core/seeder.py), [rfdb-core/rfdb_core/triplestore/oxigraph.py](rfdb-core/rfdb_core/triplestore/oxigraph.py) `load_turtle`).
  This is slow and previously timed out (mitigated for now by the configurable `OXIGRAPH_LOAD_TIMEOUT`,
  default 300s). Options to brainstorm: skip re-seeding when the graph already holds the vocab
  (idempotency check), one-time offline load into a persisted Oxigraph volume, Oxigraph
  bulk-loader / native import instead of HTTP, streaming/chunked upload, or splitting vocab from
  large reference data. Decide and get user sign-off before implementing. (The Glottolog
  vocabulary itself is already hydrated and wired to `rfdbs:SourceShape` `dcterms:language`;
  this task concerns only how it is loaded, not the feature.)

- [ ] **REQUIRES BRAINSTORM — manage Julian vs Gregorian calendar dates.** Historical sources
  (especially pre-1918 Russian material) record dates in the Julian calendar, but XSD date
  datatypes carry no calendar-system information, so a value like `prism:publicationDate "1736"`
  is ambiguous about which calendar it is in. Decide how to record the calendar system and how
  to relate the as-in-source (Julian) value to a normalized (Gregorian) one — e.g. a
  calendar-system qualifier property, dual Julian/Gregorian properties, or a reified date node —
  plus backend conversion between the two. Spans schema (SHACL date fields), backend
  (validation/conversion; ties into the deferred cross-entity date-order check under
  [Platform & future](#platform--future)), and UI (date input picks a calendar; display can show
  both). Brainstorm the modeling and get user sign-off before implementing.

### Modeling & schema

Changes that touch only the SHACL shapes or the RDF model are tracked in the
schema repository, where `schema/schema.ttl` comes from. Items here need work
in this repository too.

- [ ] Complete the shape-role policy for nested shapes and helper records (see [architecture.md](docs/architecture.md#shape-role-policy)).
- [ ] Entity delete orphans its bridge nodes. `DELETE /api/v1/curator/entities` removes only `<entity> ?p ?o` (`delete_entity` in [data.py](curator-backend/api/data.py)), so an `AgentRole` owned by the deleted Work stays in the store. The update path already removes a bridge node together with its link, guarded by `FILTER NOT EXISTS` for shared nodes, over the predicates from `bridge_link_predicates()`. Reuse that query shape on delete and run it before the entity delete. The frontend sends `shapeId` on delete, but the endpoint treats it as optional. See [architecture.md](docs/architecture.md#delete-behavior-and-orphaned-helper-nodes).
- [ ] Entity delete leaves inbound links dangling: triples with the deleted entity as object stay (e.g. deleting a Person leaves `core:hasAgent` on its `AgentRole` nodes pointing at nothing). Decide between blocking the delete, cascading, or warning in the UI; the only guard today is the generic `window.confirm` in `ShapeRecordList.jsx`.
- [ ] Clean up `AgentRole` nodes already orphaned in existing stores. Before the edit-path fix, removing a connection in the editor kept the node's triples. Nothing detects them: `scripts/cleanup_files.py` and the `orphanedNodes` count of `/meta/files` cover `schema:DigitalDocument` only.
- [ ] Check bridge-node removal, re-pointing and rollback in the live editor. They are covered by `tests/curator/test_agent_role_nested.py` against an rdflib-backed store double, never against Oxigraph.
- [ ] Improve JSON-LD handling for nested forms and repeated multilingual values.
- [ ] Expand the controlled-vocabulary seed set.

### Data, search & export

- [ ] Replace OFFSET-based pagination with cursor-based SPARQL pagination for large graphs.
- [ ] **Investigate graph/explorer read-path performance (measure before optimizing).** Several
  unmeasured costs worth profiling: (a) `relation_predicates()` re-derives the relation-predicate
  set from all shapes on every `GET /api/v1/dataexplorer/graph/node` call — memoizing it on `app.state` was
  considered but is of **dubious** benefit until measured, and would need to invalidate on schema
  reload; (b) the explorer eagerly prefetches one `GET /api/v1/dataexplorer/graph/node` per collapsed node, so
  expanding a high-degree hub fans out into many concurrent requests (browser-throttled, deduped,
  but chatty) — consider a concurrency cap or hover-gated prefetch; (c) that endpoint returns a
  node's full edge list even when the client only needs a link *count*, so a cheap degree/COUNT
  (a general node-stats capability, not a bespoke route) may beat reusing `getNode` for counts.
  Profile first, then decide which — if any — are worth it. Pairs with cursor-based pagination above.
- [ ] Verify that search autocomplete also matches on alternative labels (`skos:altLabel`), not only the primary label.
- [ ] Smarter search ranking that favours edit distance without relying on a server-side cap or limit.
- [ ] Bulk import (Excel/CSV → RDF).
- [ ] Data export (RDF, JSON-LD, CSV) — the triples-only export, distinct from the full snapshot below.
  **Already served at *resource* granularity:** `GET /rdf/data/{id}` returns any single
  resource as Turtle, JSON-LD, RDF/XML or N-Triples (`?_mediatype=`, or an `Accept` header).
  What remains here is *dataset* granularity — a whole graph or a shape's worth of entities in
  one file — plus CSV, which the conneg layer deliberately does not do because a tabular
  projection of a graph needs a chosen shape, not a serializer.
- [ ] **REQUIRES BRAINSTORM — full snapshot export (schema + data + files).** A consistent,
  restorable export of the whole curated state — SHACL schema graph, the data graph(s), and the
  Garage-stored digital-copy blobs — not just a triples dump. Leaning toward a background export
  script (`curator-backend/scripts/`) producing versioned/timestamped snapshots rather than a UI feature.
  Open questions: bundle format (tar of `schema.ttl` + `data.ttl`/`data.nq` + `files/` + a manifest
  linking digital-copy IRIs → blob keys); point-in-time consistency across Oxigraph + Garage
  (snapshot ordering / brief read lock vs. reconcile-after); trigger (cron via the existing
  scheduler? on-demand CLI? both?); where snapshots land (local volume vs. a dedicated Garage
  bucket); whether it doubles as the backup/restore path; and a matching import/restore counterpart.
- [ ] Audit trail / change history.
- [ ] Pre-flight check: validate the SHACL shapes for consistency before the server starts, rejecting unsupported features/paradigms (e.g. shapes without a `targetClass`).

### Platform & future

- [ ] **DEFERRED — cross-entity date-order check.** E.g. an Expression's creation date should
  be after its Work's, and a Manifestation's after its Expression's. Too specific and strict to
  enforce now: historical dates are often approximate or uncertain. Reconsider once the
  Julian/Gregorian modelling is settled.
- [ ] **FUTURE — multi-user editing.** The editor assumes a single concurrent curator. No optimistic
  locking or transactions: concurrent edits to the same entity can interleave (delete-then-insert
  update flow in `curator-backend/api/data.py`), and cross-store operations (Oxigraph triples +
  Garage objects) are not atomic. Needed before multiple curators work simultaneously: conflict
  detection (e.g. ETag/version triple per entity) and a saga/compensation pattern for
  file-upload + triple-write. File-id minting is already race-safe (random 8-hex suffixes,
  never reused).
- [ ] **FUTURE — storage.** Evaluate LanceDB for digital-copy storage once basic PDF upload (Garage)
  ships in production. Idea: store PDF text + page embeddings alongside blobs to make scanned
  sources semantically searchable, not just downloadable. Kept low-risk by the
  `rfdb-core/rfdb_core/file_storage.py` seam — a later, isolated swap. Trade-offs: embedded (no
  separate S3 service) and unlocks semantic search, but is not blob-first, has no S3 API, and
  needs an OCR/embedding pipeline.
- [x] **DONE — decouple the services from the triplestore so Oxigraph can be swapped.**
  The `TripleStore` seam ships in [rfdb-core/rfdb_core/triplestore/](rfdb-core/rfdb_core/triplestore/):
  a runtime-checkable Protocol (`base.py`), `OxigraphStore` as the first implementation, and a
  `build_triplestore(settings)` factory keyed on the `TRIPLESTORE` env var — so a second store is a
  config change, not an edit to a handler. Both services reach the store only through
  `app.state.store`; `oxigraph_client.py` is gone. The conformance suite this item asked for is
  [tests/core/test_triplestore_contract.py](tests/core/test_triplestore_contract.py) (24 cases:
  Protocol conformance, graph-scoping strings, transport request shape/parsing, plus a live
  round-trip that skips without a store). Writing it found a real pre-existing bug: `construct()`
  caught `rdflib.exceptions.Error`, but malformed Turtle raises `BadSyntax`, which derives from
  `SyntaxError` — so the documented `ValueError` never fired.
  Still open, and deliberately deferred until a second implementation is actually attempted:
  - [ ] **Seam method naming.** The Protocol keeps today's names (`query`, `construct`, `update`,
    `load_turtle`, `clear_store`, `health`, `from_clause`, `with_clause`, `graph`) because renaming
    would touch ~20 call sites and ~12 test doubles for zero behavioural gain. Revisit when a store
    with a different scoping model is added — that is the moment names get tested against reality
    rather than taste. Candidates: `query` → `query_select` (it only does SELECT), `construct` →
    `query_construct`, and replacing the raw `from_clause()`/`with_clause()` SPARQL fragments with a
    scoping object. (Decision D6 of the modular-services refactor.)
  - [ ] **Bulk-load path** — ties into the startup bulk-load item above; `load_turtle` currently
    assumes the Graph Store Protocol.
  - [ ] **Transaction/atomicity semantics** differ across stores and are not modelled by the seam.
  - [ ] **How much to lean on plain SPARQL 1.1 vs. per-store adapters** — unanswerable until there
    are two adapters.

---

## DevOps

> Context — the digital-copy upload-first subsystem (staging → `registered/` promotion,
> `rfdb-core/rfdb_core/file_storage.py`, `curator-backend/scripts/cleanup_files.py`, `GET /api/v1/dataexplorer/meta/files`)
> is shipped and live-verified in dev. Note that after the writer/reader split the upload lands on
> `curator-backend` and the **published** download on `dataexplorer-backend` — a copy becomes
> published when a parent entity references it in RDF, never by which storage prefix holds its
> bytes. Until then it is curator working state, previewable only via
> `GET :8000/api/v1/curator/files/staged/{fileId}`. A file referenced in RDF but still under `staged/` means a
> promotion did not complete: it still downloads, but with `X-RFDB-File-State: awaiting-promotion`
> and a warning — treat that header as a signal the cleanup run below is overdue. The first two
> items below cover production deployment and operation.

- [ ] **OPERATIONAL — run the file-storage cleanup periodically:** `docker compose exec curator-backend python scripts/cleanup_files.py` (add `--dry-run` to preview). Purges abandoned staged uploads (>24h), unreferenced registered files (>24h grace), and orphaned digital-copy nodes. The Data Context Panel "File storage" section shows when counts grow.
- [x] **DONE — production digital-copy storage.** `garage` is in [docker-compose.prod.yml](docker-compose.prod.yml) on the `internal` network only (never on `edge`), with `read_only`, `no-new-privileges` and tmpfs hardening; [proxy/Caddyfile](proxy/Caddyfile) sets `request_body max_size {$RFDB_MAX_UPLOAD}` on the curator prefix, which is the only prefix that accepts uploads; the S3 wiring reaches both backends from one YAML anchor. Docs updated in [deployment.md](docs/deployment.md#production-deployment) (topology table + runbook) and this README. **Not yet deployed anywhere** — see the status note at that heading.
- [ ] Investigate what dominates build/deploy time (backend especially) and whether it can be sped up.
- [ ] Set up automated release to GitHub Releases and the GitHub package registry.
- [ ] Verify versioning and commit tagging work with GitHub Actions (automatic release from tagged commits).

---

## Non-Goals

The initial version does not aim to:

- implement a full ontology editor;
- replace SHACL authoring tools;
- provide complex graph visualization;
- automate ontology migration;
- infer inverse relations unless required by the schema;
- enforce constraints not present in SHACL;
- replace expert data curation.
