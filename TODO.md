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
- [ ] UI language switch (English / Italian). The schema names shapes and fields in both languages (`sh:name`), and vocabulary concepts carry an Italian `rdfs:label` with an English `skos:altLabel`, but the backend picks English for every label (`_english_first` in [schema_extractor.py](rfdb-core/rfdb_core/schema_extractor.py), `preferred_label` in [graph.py](dataexplorer-backend/api/graph.py)). Let the user pick the language in each UI, defaulting to the browser's: the shapes and entity endpoints return every language (or take a `lang` parameter), and the frontends choose. Cover shape and field names, controlled-vocabulary labels, and entity labels.
- [ ] **Editor: stale and racing requests.** Three fetches can show the wrong data:
  - the record list's search (`ShapeRecordList.jsx`) fires on every keystroke, has no stale
    guard, so an older answer can land last, and only logs errors to the console;
  - the inspector's record fetch (`ValidationPanel.jsx`) has the same pattern;
  - on the first render after Edit, `recordLoading` is still false, so the form briefly
    renders the previous record (the comment in `App.jsx` marks it). Deriving the loading
    state as "a record is selected and the loaded one is not it" closes it.
  Fix with an ignore flag per effect (as the record fetch in `App.jsx` now has) and a short
  debounce on the search.
- [ ] **Editor: prefix compaction picks the first match.** `compactIri` (`utils/prefixes.js`)
  returns the first matching namespace instead of the longest; latent, since no namespaces nest
  today.
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
  This is slow, and it needs the configurable `OXIGRAPH_LOAD_TIMEOUT` (default 300s) to finish
  at all. Options to brainstorm: skip re-seeding when the graph already holds the vocab
  (idempotency check), one-time offline load into a persisted Oxigraph volume, Oxigraph
  bulk-loader / native import instead of HTTP, streaming/chunked upload, or splitting vocab from
  large reference data. Decide and get user sign-off before implementing. (The Glottolog
  vocabulary itself is wired to `rfdbs:SourceShape` `dcterms:language`;
  this task concerns only how it is loaded, not the feature.) Dev stacks seed
  `data/glottolog_language_dev_subset.ttl` instead, which hides the cost there; production
  still loads the full file.

- [ ] **REQUIRES BRAINSTORM + USER APPROVAL — separate the reference data from the records.**
  Today one Oxigraph store, on one volume, holds both the data that rarely changes (the
  schema, the controlled vocabulary, Glottolog and any other external vocabulary such as
  LRMoo) and the records curators edit. So wiping the records means wiping everything and
  reloading it all: after a live-suite run, a reset (`RESET_DATA_ON_STARTUP` erases every
  graph) or a fresh install, the reseed takes over 90 s. Goal: the reference data is loaded
  once and kept, the records can be wiped on their own, and a schema change replaces only
  the schema's data, never LRMoo or Glottolog. Options to study: two Oxigraph services, each
  with its own volume (the reader would query both, and SHACL validation needs both: check
  whether that means a federated `SERVICE` query or loading the reference data in memory);
  or one store with each source in its own named graph, wiped one graph at a time. Decide
  the layout, then revisit the startup-load item above, the seeder, the wipe procedure in
  `.agent-defs/testing.md` and `meta/graphs`.

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
schema repository, where this repository's `schema/rfdb_schema.ttl` comes from. Items here need work
in this repository too.

- [ ] Complete the shape-role policy for nested shapes and helper records (see [architecture.md](docs/architecture.md#shape-role-policy)).
- [ ] Entity delete orphans its bridge nodes. `DELETE /api/v1/curator/entities` removes only `<entity> ?p ?o` (`delete_entity` in [data.py](curator-backend/api/data.py)), so an `AgentRole` owned by the deleted Work stays in the store. The update path already removes a bridge node together with its link, guarded by `FILTER NOT EXISTS` for shared nodes, over the predicates from `bridge_link_predicates()`. Reuse that query shape on delete and run it before the entity delete. The frontend sends `shapeId` on delete, but the endpoint treats it as optional. See [architecture.md](docs/architecture.md#delete-behavior-and-orphaned-helper-nodes).
- [ ] Entity delete leaves inbound links dangling: triples with the deleted entity as object stay (e.g. deleting a Person leaves `core:hasAgent` on its `AgentRole` nodes pointing at nothing). Decide between blocking the delete, cascading, or warning in the UI; the only guard today is the generic `window.confirm` in `ShapeRecordList.jsx`.
- [ ] Clean up `AgentRole` nodes orphaned in existing stores: nodes no record links to any more, whose triples remain. Nothing detects them: `scripts/cleanup_files.py` and the `orphanedNodes` count of `/meta/files` cover `schema:DigitalDocument` only.
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
- [ ] **DECIDE — reads when the store is down.** The reader's list, count and search routes
  answer with empty results when Oxigraph is unreachable (now with a logged warning), while
  fetching one entity, the graph and the meta routes answer 503. Empty keeps the sidebar
  usable, but the editor then shows "no records", which reads as data loss. Options: 503
  everywhere and a "store unreachable" state in both UIs, or keep empty answers and add a
  store-health indicator (see the Data Context Panel's Phase 2 under [UI](#ui)).
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

- [ ] **FUTURE (next release) — base IRIs with any path.** `DATA_BASE_URI` and
  `SCHEMA_BASE_URI` are settings, but the IRIs only resolve when their paths are
  `/rdf/data/` and `/rdf/schema/`: the proxy forwards only `/rdf/*`
  (`proxy/Caddyfile`), and the reader's routes are fixed in
  `dataexplorer-backend/api/resource.py`. With another path, records still save
  and edit, but visiting an IRI, the links on the reader's HTML pages and opening
  uploaded PDFs from the editor all 404. To plan: register the reader's routes at
  each base's path, have the proxy route those paths from the environment, and
  check the frontends' `/rdf/` assumptions (`resolveFileUrl`, `fileDownloadUrl`
  and `entityPageUrl` in `curator-frontend/src/api/client.js`, the graph
  explorer's client). Until then [docs/custom-schema.md](docs/custom-schema.md)
  tells a fork to keep the paths.
- [ ] **DEFERRED — cross-entity date-order check.** E.g. an Expression's creation date should
  be after its Work's, and a Manifestation's after its Expression's. Too specific and strict to
  enforce now: historical dates are often approximate or uncertain. Reconsider once the
  Julian/Gregorian modelling is settled.
- [ ] **FUTURE — multi-user editing.** The editor assumes a single concurrent curator. No optimistic
  locking or transactions: concurrent edits to the same entity can interleave (delete-then-insert
  update flow in `curator-backend/api/data.py`), and cross-store operations (Oxigraph triples +
  Garage objects) are not atomic. Needed before multiple curators work simultaneously: conflict
  detection (e.g. ETag/version triple per entity) and a saga/compensation pattern for
  file-upload + triple-write. File ids are random 12-hex suffixes; nothing checks them against
  existing ones.
- [ ] **FUTURE — storage.** Evaluate LanceDB for digital-copy storage once basic PDF upload (Garage)
  ships in production. Idea: store PDF text + page embeddings alongside blobs to make scanned
  sources semantically searchable, not just downloadable. Kept low-risk by the
  `rfdb-core/rfdb_core/file_storage.py` seam — a later, isolated swap. Trade-offs: embedded (no
  separate S3 service) and unlocks semantic search, but is not blob-first, has no S3 API, and
  needs an OCR/embedding pipeline.
- [ ] **Triplestore seam: open points for a second store.** The `TripleStore` seam is in
  [rfdb-core/rfdb_core/triplestore/](rfdb-core/rfdb_core/triplestore/): a runtime-checkable
  Protocol (`base.py`), `OxigraphStore` as the only implementation, and a
  `build_triplestore(settings)` factory keyed on the `TRIPLESTORE` env var, so a second store is a
  config change, not an edit to a handler. Both services reach the store only through
  `app.state.store`, and [tests/core/test_triplestore_contract.py](tests/core/test_triplestore_contract.py)
  is the conformance suite. Deferred until a second implementation is actually attempted:
  - [ ] **Seam method naming.** The Protocol uses the Oxigraph client's names (`query`, `construct`, `update`,
    `load_turtle`, `clear_store`, `health`, `from_clause`, `with_clause`, `graph`) because renaming
    would touch ~20 call sites and ~12 test doubles for zero behavioural gain. Revisit when a store
    with a different scoping model is added — that is the moment names get tested against reality
    rather than taste. Candidates: `query` → `query_select` (it only does SELECT), `construct` →
    `query_construct`, and replacing the raw `from_clause()`/`with_clause()` SPARQL fragments with a
    scoping object.
  - [ ] **Bulk-load path** — ties into the startup bulk-load item above; `load_turtle` currently
    assumes the Graph Store Protocol.
  - [ ] **Transaction/atomicity semantics** differ across stores and are not modelled by the seam.
  - [ ] **How much to lean on plain SPARQL 1.1 vs. per-store adapters** — unanswerable until there
    are two adapters.

---

## DevOps

> Context — the digital-copy upload-first subsystem (staging → `registered/` promotion,
> `rfdb-core/rfdb_core/file_storage.py`, `curator-backend/scripts/cleanup_files.py`, `GET /api/v1/dataexplorer/meta/files`)
> is shipped and live-verified in dev. The upload lands on
> `curator-backend` and the **published** download on `dataexplorer-backend` — a copy becomes
> published when a parent entity references it in RDF, never by which storage prefix holds its
> bytes. Until then it is curator working state, previewable only via
> `GET :8000/api/v1/curator/files/staged/{fileId}`. A file referenced in RDF but still under `staged/` means a
> promotion did not complete: it still downloads, but with `X-RFDB-File-State: awaiting-promotion`
> and a warning — treat that header as a signal the cleanup run below is overdue. The first two
> items below cover production deployment and operation.

- [ ] **OPERATIONAL — run the file-storage cleanup periodically:** `docker compose exec curator-backend python scripts/cleanup_files.py` (add `--dry-run` to preview). Purges abandoned staged uploads (>24h), unreferenced registered files (>24h grace), and orphaned digital-copy nodes. The Data Context Panel "File storage" section shows when counts grow.
- [x] **DONE — production digital-copy storage.** `garage` is in [docker-compose.prod.yml](docker-compose.prod.yml) on the `internal` network only (never on `edge`), with `read_only`, `no-new-privileges` and tmpfs hardening; [proxy/Caddyfile](proxy/Caddyfile) sets `request_body max_size {$RFDB_MAX_UPLOAD}` on the curator prefix, which is the only prefix that accepts uploads; the S3 wiring reaches both backends from one YAML anchor. Documented in [deployment.md](docs/deployment.md#production-deployment) (topology table + runbook). **Not yet deployed anywhere** — see the status note at that heading.
- [ ] **FUTURE — scale the production deployment.** The curator runs one uvicorn worker
  (`docker-compose.prod.yml`), enough for a few curators: every worker runs the startup seed,
  so two workers load the vocabulary twice at once into an Oxigraph capped at one CPU. Before
  adding workers: seed once with `scripts/seed.py` and set `SEED_VOCAB_ON_STARTUP=false` for
  the web process, then raise `--workers` and the CPU and memory limits of the curator and of
  Oxigraph together. The reader seeds nothing, so its workers can grow on their own. Past one
  host, the single Oxigraph and Garage instances are the limit; multi-user editing (above)
  also has to land before several curators write at once.
- [ ] **CHECK — `no-cache` on the frontends' HTML.** In `proxy/spa.Caddyfile`, `header
  /index.html Cache-Control "no-cache"` probably never matches `/` or a deep link: `header`
  runs before the `try_files` rewrite, so it sees the original path. If so, browsers may keep
  an old `index.html`, and with it old asset names, after a deploy. Check against a running
  production build (`curl -I` on `/` and on a deep link), then fix by setting `no-cache` on
  every path outside the immutable assets.
- [ ] **DECIDE — the script that makes `data/glottolog_language_dev_subset.ttl`.** The file is
  committed, but the script that cut it from `data/glottolog_language.ttl` is not, so nobody
  can rebuild it or change the language list. Decide whether to commit it (and where:
  `scripts/`, or beside the data), and whether it should run as part of updating Glottolog.
- [ ] **Live HTTP suite: two gaps.** `tests/curator/test_backend_api_data.py` cannot tell
  whether the stack serves the checkout under test: expose the served commit (or a source
  hash) on `/health` in dev and have the fixture compare it. And its payloads hardcode the
  rfdb base IRIs and shapes, so it runs only against the rfdb schema: read the base IRIs
  from `meta/schema`, and skip the rfdb-specific scenarios on another schema.
- [ ] **Test structure cleanup.** Store doubles have drifted: test files each write their own
  capturing or rdflib-backed store; share one of each. The live-store probes in
  `tests/core/test_triplestore_contract.py` and `tests/curator/test_validation_merge_planner.py`
  only open a TCP connection, once: share one helper that checks `/health`, as the live
  suite's fixture now does. App-import helpers are repeated in four files; a per-suite
  fixture would do. The docstring of `tests/curator/test_seeder.py` lists only the
  `VOCAB_PATH` cases, though the file also tests `bootstrap_store`.
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
