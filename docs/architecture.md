# RFDB Curator — Architecture

How the pieces fit together: the schema-driven pipeline, the writer/reader service
split, frontend responsibilities, how SHACL shapes are extracted into form and
validation metadata, how validation and delete behave, and the software stack. For what
the application requires of a schema see [data-model.md](data-model.md); for
the day-to-day workflow of changing schema/data/code see [development.md](development.md).

---

## Core Architectural Principle

The `rfdb-curator` application is schema-driven by design, and the frontend carries no
hard-coded assumptions about the current entity model.

Data flows through the stack like this:

```text
schema/rfdb_schema.ttl
    ↓
schema extractor (rfdb-core, loaded by both services)
    ↓
normalized form schema
    ↓
React dynamic form rendering
    ↓
JSON-LD payload
    ↓
RDF graph generation
    ↓
SHACL validation
    ↓
Oxigraph persistence
```

This separation matters because the RFDB schema evolves over time: the editor keeps working when shapes, properties, labels, target classes, or ontology alignments change.

---

## Service Split

There are two API services and one shared library. The line between them is *whether
an operation mutates the store*:

| | `curator-backend` (:8000) | `dataexplorer-backend` (:8001) |
|---|---|---|
| Writes | **all of them** — create/update, delete, upload staging | none |
| Reads | shape metadata + forms only | everything else |
| SHACL validator | yes | no |
| Seeding / reset | yes | no |
| `app.state` | `schema_extractor`, `store`, `storage`, `shacl_validator`, `shape_dep_graph`, `seed_report` | `schema_extractor`, `store`, `storage` |

Both talk to the same Oxigraph and the same object-storage bucket, and neither imports
the other. `dataexplorer-backend` does not `depends_on` the curator in Compose: the
independence is the point, and a read-only deployment runs the reader alone.

**`rfdb-core`** holds what both need — the `TripleStore` seam, the schema extractor, the
object-storage seam, the CURIE map, the IRI guard, the digital-copy state snapshot, the
shared settings base, and the FastAPI edge wiring (CORS, access log, storage-error
handling). It is framework-free apart from one module behind a `web` extra, so neither
service inherits the other's web stack.

Two things about the split are worth stating explicitly:

- **Both services serve the shape catalogue, and the payloads are identical.** One
  implementation (`rfdb_core.shapes`) over one schema, `readOnly` flags included.
  `READ_ONLY_SHAPES` is policy metadata about *which shapes are editable*, which any client
  needs to render a UI, so the editor takes its sidebar from the reader and keeps it during
  a writer outage. The obligation is operational: give both services the same value (a
  single Compose YAML anchor does that).
- **Only the curator waits for the store at startup.** It is about to clear and seed,
  and a connection dropped mid-wipe is unrecoverable, so it polls `health()` until the
  store answers. For the reader an unreachable store is a per-request condition (503, or
  empty results) that resolves itself — blocking startup on it would only reduce
  availability.

## Backend Responsibilities

Together, the two services turn RDF and SHACL semantics into stable API structures the
frontend can consume. They:

- loads the SHACL schema at `SCHEMA_PATH` (`schema/rfdb_schema.ttl` in this repository)
- extracts all `sh:NodeShape` definitions
- extracts field descriptors from `sh:property` blocks
- preserves shape labels and descriptions
- resolves prefixes and compact IRIs
- detects target classes
- detects each field's value kind: literal, IRI, linked entity, or fixed value
- detects cardinality and repeatability
- detects datatype alternatives from `sh:or`
- detects linked shapes from `sh:node`
- detects expected target classes from `sh:class`
- validates submitted payloads with pySHACL
- merges referenced entities into validation graphs when needed
- loads RDF data into Oxigraph
- queries entities by shape
- provides autocomplete for linked-resource fields
- returns human-readable validation errors where it can
- exposes operational metadata such as prefix maps and named-graph status

---

## API Reference

Every endpoint, and which service owns it. The behaviors behind several of these
(validation merge, delete, the metadata endpoints) are explained in the sections
further down.

The URL space is split into **two spaces with different contracts**, which is the single
most important thing to know before adding a route:

| Space | Owner | Contract |
|---|---|---|
| `/rdf/…` | reader | **The data.** Public, permanent, **unversioned** identifiers. These URLs are stored *inside triples*, so they can never move. |
| `/api/v1/{service}/…` | each service | **Our own apps' operational surface.** Versioned, and named after the service that owns it. |

Naming the owner in `/api/` paths is deliberate. It means no path can mean two things
depending on which service answers it, a wrong-service request fails as a legible `404`
instead of a `405`, a reverse proxy routes the whole API by prefix with no method
awareness, and a read-only deployment simply omits the `/api/v1/curator/*` namespace
rather than returning `403` per route. That costs nothing in public-contract terms
precisely *because* the durable, third-party-facing identifiers live in `/rdf/`.

### The data space — `/rdf/…` (served by dataexplorer-backend, :8001)

| Method | Path | Description |
|---|---|---|
| `GET` | `/rdf/data/{id}` | **Content-negotiated dereference** of an entity: `text/html`, `text/turtle`, `application/ld+json`, `application/rdf+xml`, `application/n-triples`. Describes the resource in *both* directions, so the graph is walkable from any IRI. `?_mediatype=` overrides `Accept`, `?_profile=alt` lists the representations; `Vary: Accept` is set. |
| `GET` | `/rdf/data/{fileId}/content` | The bytes of a **published** digital copy — what `schema:contentUrl` points at |
| `GET` | `/rdf/schema/{ShapeName}` | A SHACL shape's own definition, served from the loaded schema. **RDF only** — see below |

An entity's IRI *is* its URL: `https://rosfeatr.eu/rdf/data/{id}`, and production serves
that host, so a browser pointed at any identifier in the data gets a readable page:
label, types, literal properties, and click-through links in both directions. A
machine client asking for `*/*` gets Turtle instead — this is a data space, so the
default is machine-readable. An `Accept` we cannot satisfy at all still falls back to
Turtle rather than answering `406`, which would make a persisted identifier look broken.

Three properties of that surface are load-bearing rather than cosmetic:

- **Links on the HTML page are path-only.** A persisted IRI names `rosfeatr.eu`, but a dev
  reader answers on `localhost:8001`, so linking the IRI as-is would send a developer's
  click to the public internet. The path each link uses is *derived from the namespace*
  (`DATA_BASE_URI` is `https://rosfeatr.eu/rdf/data/`, and the router is mounted at `/rdf`) —
  that identity is what makes these URLs cool, and deriving it keeps the links right if the
  namespace ever moves. Same re-basing the frontend does for `schema:contentUrl`.
- **A variant URL percent-encodes its media type.** `?_mediatype=application/ld+json`
  does *not* work — a query string is form-encoded, so the `+` arrives as a space. Every
  URL the service emits (`Content-Location`, the `Link` alternates, the page's own links)
  writes `%2B`. A hand-typed `+` is still understood, since no media type contains a space.
- **`/rdf/schema/{ShapeName}` serves no HTML.** A shape's description is mostly blank nodes
  — a shape *is* its property descriptors — which a flat subject-centric page would
  silently drop. A browser gets Turtle; an explicit `?_mediatype=text/html` gets a `400`
  naming what the route does serve. The rendered form of the shape catalogue is the
  editor's, fed by `/api/v1/dataexplorer/shapes`.

**Content negotiation by profile (ConnegP conventions, not Prez).** `?_profile=alt` returns
the list of available representations, itself negotiable in any of the five formats. It is
described with DCMI terms — `dcterms:hasFormat` is defined as the same resource in another
format — rather than with the ALTR vocabulary Prez uses; adopting Prez/pyLDAPI is an
explicit non-goal. `?_profile=rfdb` names the default view, and an unknown token is a `400`,
matching `_mediatype`: a client that asked for something by name deserves an error rather
than a silent substitution. Profile *tokens*, not profile URIs — a URI minted under `/rdf/`
would be a permanent public identifier we then owe a representation, for no current
consumer. Every response also carries plain RFC 8288 discovery: `rel="canonical"` plus one
`rel="alternate"` per variant.

### `curator-backend` — :8000 (writes)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness/readiness check (Oxigraph status + seed report) |
| `GET` | `/api/v1/curator/shapes` | NodeShapes with metadata, field descriptors and the `readOnly` flag — **identical to the reader's** |
| `GET` | `/api/v1/curator/forms?shapeId=...` | Generated form schema for one shape. Writer-only: form fields exist to drive an editing form |
| `POST` | `/api/v1/curator/entities` | Create (no `@id`) or update (`@id`) one record and its helper nodes (JSON-LD → SHACL validate → Turtle load) |
| `DELETE` | `/api/v1/curator/entities?id=<iri>` | Delete a record and its helper nodes; 404 if it does not exist, 409 while other records point to it |
| `POST` | `/api/v1/curator/validate` | Dry-run SHACL validation without persisting |
| `POST` | `/api/v1/curator/files/staged` | Stage an uploaded digital copy (e.g. a PDF) before it is attached to a record |
| `GET` | `/api/v1/curator/files/staged/{fileId}` | Preview a staged, not-yet-submitted copy (mode-gated, curator-only) |

Nothing here is published under `/rdf/`: permanent identifiers must not resolve to the
one component a read-only deployment omits.

### `dataexplorer-backend` — :8001 (reads)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness/readiness check (store status only) |
| `GET` | `/api/v1/dataexplorer/shapes` | NodeShapes with metadata, field descriptors and the `readOnly` flag — **byte-identical to the curator's** |
| `GET` | `/api/v1/dataexplorer/entities` | Paginated entity list for a shape, with text filter |
| `GET` | `/api/v1/dataexplorer/entities/counts` | Per-shape entity counts |
| `GET` | `/api/v1/dataexplorer/entities/search` | Autocomplete for linked-resource fields |
| `GET` | `/api/v1/dataexplorer/entities/get?id=<iri>` | All triples for one entity, in the editor's JSON shape (the RDF sibling is `GET /rdf/data/{id}`) |
| `GET` | `/api/v1/dataexplorer/graph/node` | Schema-aware graph traversal (one node + its relation edges) |
| `GET` | `/api/v1/dataexplorer/meta/schema` | The loaded schema's version (`owl:versionInfo`), plus the deployment settings clients render with: `projectName`, `dataBaseUri`, `schemaBaseUri`, `externalIdLinks` |
| `GET` | `/api/v1/dataexplorer/meta/prefixes` | CURIE prefix-to-namespace map the schema declares (`sh:declare`) |
| `GET` | `/api/v1/dataexplorer/meta/graphs` | Named graphs with triple/term counts and advisory config warnings |
| `GET` | `/api/v1/dataexplorer/meta/files` | Digital-copy storage stats (staged/registered/orphans) |

**Both services serve the same shape catalogue, from one implementation**
(`rfdb_core.shapes`), `readOnly` flags included; see [Service Split](#service-split) for
why. Both must be given the same `READ_ONLY_SHAPES`, which `docker-compose.yml`
guarantees with a single YAML anchor rather than two editable literals.

**An entity IRI always travels as `?id=`, never as a path segment** — on both services, and
matching `/graph/node?id=`. Two reasons. A path parameter would have to be greedy (`{iri:path}`,
because an encoded IRI's `%2F` is decoded before route matching), and a greedy parameter under
`/entities/` silently swallows `/entities/search` and `/entities/counts` unless every literal route
happens to be registered first — an invariant that would live in `app.py`, files away from the routes
it governs. Second, a path-encoded IRI needs double-encoding to survive, and a bare local name fails
the unsafe-IRI guard with a 400 that reads like a data problem. A test on each service asserts no
route uses `:path`, so the collision is impossible rather than merely guarded.

`entities/get` puts a verb in a URL, which is deliberate: this is the operational surface, where the
HTTP method alone cannot distinguish a single-entity lookup from the collection. The
resource-oriented address for the same entity is `GET /rdf/data/{id}`, which is where RESTful
identity belongs.

The two `/health` payloads differ:

- curator: `{ "status": "ok", "oxigraph": "up" | "down", "seed": { ... } | null }`
- dataexplorer: `{ "status": "ok", "store": "up" | "down" }` — no seed report, because it
  never seeds.

Note that a digital copy is **uploaded** through the curator and **downloaded** through
the reader: staging writes bytes, serving them does not.

The two download routes are not duplicates — they serve different lifecycle states, and
which one applies is an **RDF** question, never a storage-prefix one:

| State | Route | Why there |
|---|---|---|
| Staged, no parent entity yet | `GET :8000/api/v1/curator/files/staged/{fileId}` | One curator's in-progress working state. Mode-gated, and never handed out by the public reader. |
| Published (a parent references it) | `GET :8001/rdf/data/{fileId}/content` | A real resource. Answers `X-RFDB-File-State: registered`. |

The reader refuses anything unreferenced with a `404`, so unsubmitted uploads are not
public. When a file *is* referenced but its bytes are still under `staged/` — the
promotion after the entity write did not complete — it is served with
`X-RFDB-File-State: awaiting-promotion` and a warning, rather than silently, so a stalled
`scripts/cleanup_files.py` is visible instead of latent. If the store is unreachable the
reader falls back to `registered/` presence alone (`registered-unverified`); a
staged-only file gets a `503` rather than risk publishing working state during an outage.

This is also why `contentUrl` resolves cleanly with no cross-service knowledge: staging
returns the staged path on the writer, and the submit path rewrites it to the published
path on persist, so a client always resolves the relative URL against the origin that
gave it the document.

Since both services are FastAPI, the always-current interactive reference is served at
`/docs` (Swagger UI) on each, with the raw schema at `/openapi.json`; the tables above
are a convenience overview and can drift from the running apps.

---

## Frontend Responsibilities

The frontend renders editorial workflows from the shape metadata the API provides. It:

- lists available shapes in the navigation
- shows per-shape entity counts
- renders dynamic forms from `/api/v1/curator/forms`
- marks required fields clearly
- distinguishes single-valued from repeatable fields
- supports language-tagged literal inputs
- supports date-precision choices
- supports IRI inputs and linked-record selectors
- offers autocomplete for relation fields
- preserves `@id` and `@type` during editing
- shows compact IRIs alongside labels
- surfaces form-level and field-level validation errors
- supports dry-run validation before save
- shows RDF triples for record inspection
- avoids regenerating helper-node IRIs on update

### Which backend each frontend talks to

The two apps resolve `/api` differently, because only one of them writes.

**`graphexplorer-frontend`** (Vite listens on :5174 inside the container; dev compose
publishes it to the host at :80, matching how a browser reaches it in production) — a single
Vite proxy rule sends all of `/api` to
`dataexplorer-backend:8001`. Everything it calls is a read, so it needs no second base and no
knowledge of the curator. That is what makes it the outage-resilient surface: it keeps working
with the write tier stopped.

**`curator-frontend` (:5173)** — talks to both tiers, over one origin. Vite's proxy keys on path
prefixes, and every path names the service that owns it, so the dev server routes both tiers by
prefix — the same three rules `proxy/Caddyfile` applies in production:

| | Prefix | Dev routing |
|---|---|---|
| Writes + writer-only reads | `/api/v1/curator/*` | Vite proxy → `curator-backend:8000` |
| Operational reads | `/api/v1/dataexplorer/*` | Vite proxy → `dataexplorer-backend:8001` |
| The data space (entity pages, file content) | `/rdf/*` | Vite proxy → `dataexplorer-backend:8001` |

Both bases (`VITE_API_BASE`, `VITE_READ_API_BASE`) are therefore relative in dev *and* in
production, and that is the property worth protecting: **no browser-facing value names a host**, so
one config runs on localhost, on a LAN IP, through an SSH tunnel, or behind a domain, and the app's
own frontends need no `CORS_ORIGINS` entry at all.

An absolute base such as `http://localhost:8001` breaks the stack on any other host: a remote dev
host serves the bundle, the browser resolves every read against *its own* machine, and the editor
reports `Failed to load shapes: Network Error`. Set a non-empty base only for a deployment whose
reader genuinely sits on another origin — which then needs the matching `CORS_ORIGINS` entry on
that reader.

Two consequences worth knowing:

- **The editor gets its shape catalogue from the reader; only `forms` stays on the writer.** The
  reader serves the identical catalogue, `readOnly` flags included, so the editor starts, browses
  and searches through a writer outage. Only *editing* needs the writer, and `forms` is
  writer-only because form fields exist to drive an editing form.
- **File links resolve per lifecycle state, not per configured base.** `resolveFileUrl` picks the
  base from the path the backend handed back: `/api/v1/curator/files/staged/{id}` → writer,
  `/rdf/data/{id}/content` → reader. See the digital-copy note in the API reference above.

### In production: one origin, and a mode

Production collapses all of that to **one origin**. A Caddy edge terminates TLS and routes by
path prefix, so both frontends are built with empty API bases and every call is same-origin —
no CORS anywhere:

| Prefix | Service |
|---|---|
| `/api/v1/curator/*` | curator-backend |
| `/api/v1/dataexplorer/*` · `/rdf/*` | dataexplorer-backend |
| `/explorer/*` | graphexplorer-frontend |
| everything else | curator-frontend |

That the edge config is a plain prefix table is the point of naming the owner in each path: a
path served by two services and split only by method is something no prefix-keyed proxy, Vite's
or Caddy's, can route.

Because the partition is by owner, **which kind of instance this is becomes a deploy-time
choice**: a read-only deployment omits the `/api/v1/curator/*` namespace entirely rather than
deploying a writer that answers 403 per route. Compose profiles gate exactly the two curator
services; everything else runs in both modes. Runbook and the invariants in
[deployment.md](deployment.md#deploy-modes--read-vs-full).

---

## SHACL Extraction Details

The schema extractor treats `sh:NodeShape` as the primary source for form definitions.
The SHACL terms it reads, and what each one contributes:

```text
sh:NodeShape
    Defines a record/form type.

sh:targetClass
    Defines the RDF class or classes targeted by a shape.

sh:class
    Defines the expected class of a linked resource or an additional class constraint.

sh:property
    Defines a form field.

sh:path
    Defines the RDF predicate for the field.

sh:minCount
    Defines required cardinality.

sh:maxCount
    Defines maximum cardinality. `sh:maxCount 1` means single-valued.

sh:datatype
    Defines literal datatype.

sh:nodeKind sh:IRI
    Defines IRI-valued fields.

sh:or
    Defines alternative constraints, often used for alternative literal datatypes.

sh:node
    Points to another shape, useful for generating linked-record selectors.

sh:description
    Provides field-level or shape-level help text.

sh:uniqueLang
    Prevents duplicate language tags for values of the same property.

sh:closed true
    Indicates that records should not contain properties outside the shape definition.

sh:hasValue
    Defines a fixed required value, for example a required `rdf:type`.
```

The extractor keeps enough information for both form rendering and validation feedback.

---

## Helper/Bridge Shape Classification

Shape behavior in the UI is schema-driven, not hardcoded per class name.

- If a shape declares a `sh:property` with `sh:path rdfs:label`, the extractor classifies it as a `standalone-entity`.
- If a shape has no `rdfs:label` property, the extractor classifies it as a `helper-bridge` shape.
- If a parent property uses `sh:node` to point at a `helper-bridge` shape, the form generator exposes that field as a nested inline editor instead of a normal top-level linked entity form.

`rfdbs:AgentRoleShape` is the current example of this pattern. It is referenced from `core:hasAgentRole`, defines no `sh:property` whose `sh:path` is `rdfs:label`, and is therefore edited inline as a bridge between the parent Work/Expression and the linked `Person`/`Role` records.

When changing this behavior, check both the SHACL shape and the resulting `/api/v1/dataexplorer/shapes` metadata before changing frontend code. A shape can look label-like because it has a shape-level `rdfs:label` for display, but the classifier only cares whether the shape defines an RDF property with `sh:path rdfs:label`.

---

## Validation Merge Behavior

`POST /api/v1/curator/entities` validates against a graph that includes:

- the submitted payload
- relevant referenced entities already present in the store
- transitively linked helper nodes, bounded by shape connectivity (the shape dependency graph is followed fully, with per-path cycle prevention rather than a fixed depth limit)

This is necessary for incremental top-down editing.

Example:

```text
Work
    → AgentRole
        → Person
        → Role
```

If a later payload references the Work but does not repeat the AgentRole, Person, and Role nodes, validation still has enough context to avoid false negatives.

---

## What a Save and a Delete May Touch

A save (`POST /api/v1/curator/entities`) writes one record and its helper nodes:

- Without `@id` it creates a record, named by the backend. With `@id` it updates
  that record, which must exist (404 otherwise).
- It describes the record, its helper nodes and its digital copies only. Other
  records are cited by a bare `@id`; describing one is refused (422), so a save can
  never write onto a record it did not validate as itself.
- A helper node belongs to the one record linking it. For each helper-node
  predicate the save carries, or lists in `originalTriples`, the payload is the
  whole list: a node with an IRI must already be this record's (409 otherwise) and
  its stored triples are replaced by the payload's; a new node comes as a blank
  node and is named by the backend; a stored node the payload drops is deleted.
- Every other field of the shape the save carries replaces the stored values. A field
  the save leaves out is replaced only when `originalTriples` lists it, which is how the
  editor says it emptied the field.

A delete (`DELETE /api/v1/curator/entities?id=<iri>`) removes the record and its
helper nodes. It is refused (409) while another record points to it, directly or
through one of that record's helper nodes (a Person in a Work's `AgentRole`); the
message names up to ten of them. Digital copies are left for
`scripts/cleanup_files.py`.

Both run as several store requests with no transaction across them (the deletes,
then the load). Each takes a snapshot of the record and its helper nodes first; if
any request fails, the snapshot is loaded back and the route answers 503, so a
failure part-way does not leave a record half-written.

---

## Shape-Role Policy

Nested shapes fall into two roles, and the editor treats them differently:

- **Standalone entity** — a reusable entity with independent meaning and lifecycle
  (Person, Role, Place, Holding Organization).
- **Helper bridge** — a structural node that is mainly meaningful in relation to another
  entity (`AgentRole`).

The distinction shapes several behaviors: the creation UI, deletion, updates,
autocomplete, cascade cleanup, and how the validation graph is expanded.

---

## Metadata API

Three read-only metadata endpoints back the Data Context Panel
(`curator-frontend/src/components/DataContextPanel.jsx`). All are implemented in
`dataexplorer-backend/api/meta.py`, registered in `dataexplorer-backend/app.py`, and
covered by `tests/dataexplorer/test_api_meta.py`.

```text
GET /api/v1/dataexplorer/meta/schema     — the loaded schema's version, project name, base IRIs, external-ID link templates
GET /api/v1/dataexplorer/meta/prefixes   — CURIE prefix map declared by the schema
GET /api/v1/dataexplorer/meta/graphs     — named graphs, triple counts, config warnings
GET /api/v1/dataexplorer/meta/files      — digital-copy storage stats
```

### Prefix Metadata

`GET /api/v1/dataexplorer/meta/prefixes` returns the CURIE prefix→namespace map the schema declares with `sh:declare`:

```json
{
  "prefixes": {
    "rfdb": "https://rosfeatr.eu/rdf/data/",
    "xsd": "http://www.w3.org/2001/XMLSchema#"
  }
}
```

The map is read from the schema's `sh:declare` declarations when the schema loads
(`rfdb_core.prefixes.declared_prefixes`), **not** from the rdflib schema graph's
`namespaces()`. rdflib pre-binds ~29 unrelated well-known vocabularies (`brick`, `dcat`, …)
into every `Graph`, which would otherwise leak into the map. The schema declares the
prefixes of the vocab, data and Glottolog files too; `curator-backend/scripts/check_prefixes.py`
(pre-commit and CI) fails when a `@prefix` in any schema or data TTL file is not declared.
A prefix declared with two namespaces stops the service at startup.

The editor hydrates its one prefix map (`utils/prefixes.js`, which `utils/jsonld.js` also
uses) from this endpoint at app startup (`curator-frontend/src/App.jsx`). A richer per-entry shape (merging JSON-LD context and runtime
config with drift `warnings`) remains a possible future enhancement — see the Data
Context Panel enhancements in the root [`TODO.md`](../TODO.md).

### Graph Metadata

`GET /api/v1/dataexplorer/meta/graphs` returns the runtime graph context, read store-wide (its SPARQL
queries run unscoped, so the panel sees every named graph, not just `DATA_GRAPH_URI`):

```json
{
  "activeGraph": "https://rosfeatr.eu/rdf/graph/",
  "graphs": [
    { "uri": "...", "count": 1234, "subjects": 200, "objects": 800, "literals": 300, "active": true }
  ],
  "totalTriples": 1234,
  "totalSubjects": 200,
  "totalObjects": 800,
  "totalLiterals": 300,
  "warnings": []
}
```

Per-graph `subjects`/`objects`/`literals` are distinct-term counts within that graph;
the store-wide `total*` distinct fields are counted once across all graphs, so they can
be smaller than the sum of the per-graph columns. `warnings` are advisory-only hints
(no `DATA_GRAPH_URI` configured, active graph empty or absent, or triples stranded in
the default graph outside the editor's scoped reads). Returns HTTP 503 if Oxigraph is
unreachable.

### File Storage Metadata

`GET /api/v1/dataexplorer/meta/files` returns digital-copy storage stats, mirroring the reconciler's
view (`curator-backend/scripts/cleanup_files.py`): RDF is the source of truth, object
storage is compared against it. Both read the same snapshot helper
(`rfdb_core.files_state.collect_file_state`) — it sits in the shared library precisely
because the endpoint is a read and the reconciler is a curator-side maintenance tool.

```json
{
  "configured": true,
  "staged":     { "count": 0, "bytes": 0, "oldestAgeS": null },
  "registered": { "count": 0, "bytes": 0 },
  "linkedNodes": 0,
  "orphanedNodes": 0,
  "unreferencedStaged": 0,
  "unreferencedRegistered": 0
}
```

Non-zero `orphanedNodes`/`unreferenced*` counts signal it is time to run the cleanup
script. When storage credentials are absent (`S3_ENDPOINT` unset) the endpoint returns
zeroed stats with `configured: false` instead of erroring, so the panel renders in
storage-less deployments.

---

## Software Stack

Each component, the tier it belongs to, how it runs in development
(`docker-compose.yml`) and in production (`docker-compose.prod.yml`), and the files
that pin its version. Version numbers live only in those files.

| Component | Tier | Development | Production | Version pinned in |
|---|---|---|---|---|
| `curator-frontend` (editor) | UI | Vite dev server on Node, `:5173`, hot reload over the bind-mounted source; proxies `/api` and `/rdf` to the backends | Static build, made with the same Node image, served by Caddy; the site root in `full` mode | `curator-frontend/Dockerfile`, `package.json`, `package-lock.json` |
| `graphexplorer-frontend` | UI | Vite dev server on Node, host port `80`; proxies `/api` and `/rdf` to the reader | Static build served by Caddy, under `/explorer/`, or at the site root of a read-only instance | `graphexplorer-frontend/Dockerfile`, `package.json`, `package-lock.json` |
| `proxy` | Web | Not deployed: the Vite servers proxy | Caddy: TLS, routing by path prefix; the only service on ports 80 and 443 | `docker-compose.prod.yml`, `proxy/Caddyfile` |
| `curator-backend` (writer) | Server | uvicorn with `--reload` over the bind-mounted source, `:8000` | uvicorn from the image, one worker; `full` mode only | `curator-backend/Dockerfile`, `pyproject.toml`, `uv.lock` |
| `dataexplorer-backend` (reader) | Server | uvicorn with `--reload`, `:8001` | uvicorn from the image, several workers | `dataexplorer-backend/Dockerfile`, `pyproject.toml`, `uv.lock` |
| `rfdb-core` | Server library | Bind-mounted into both backends, reloaded with them | Installed in both backend images | `rfdb-core/pyproject.toml`, `uv.lock` |
| Oxigraph | Store | Published on `:7878` | Internal network only | both compose files |
| Garage | Store | S3 API published on `:3900` | Internal network only | both compose files, `garage.toml` |

Node and Vite never run in production: Node builds the static files and runs the
development servers. CI pins Node, Python and uv in `.github/workflows/`. How
images and runtimes are pinned, and the two stores' exceptions, is in
[`.agent-defs/repo-specific.md`](../.agent-defs/repo-specific.md) ("Version pins").

- **Oxigraph** is the RDF triple store and the source of truth for graph data. All
  instance data lives in one named graph, `DATA_GRAPH_URI`: every SPARQL read uses
  `FROM <uri>`, every Turtle load targets `?graph=<uri>` through the Graph Store
  Protocol. Its volume is backed up together with Garage's; see
  [deployment.md](deployment.md#ongoing-operations-once-live).
- **Garage** is an S3-compatible object store for digital copies (uploaded PDFs). A
  file is staged first (`POST /api/v1/curator/files/staged`, previewable at
  `GET /api/v1/curator/files/staged/{fileId}`), travels in the JSON-LD payload under
  its schema-declared predicate, is promoted from `staged/` to `registered/` when the
  record is saved, and is served by the reader at `GET /rdf/data/{fileId}/content`.
  RDF stays the source of truth and storage is reconciled against it (the File
  Storage Metadata endpoint above, `scripts/cleanup_files.py`): a file is public
  because a record references it, not because of its prefix. Development wires it
  with `garage.toml` and the host-side `scripts/garage-init.sh`.

In production the proxy terminates TLS and routes by path prefix, and neither store
is reachable from outside; see
[In production: one origin, and a mode](#in-production-one-origin-and-a-mode) and the
[production runbook](deployment.md#production-deployment).
