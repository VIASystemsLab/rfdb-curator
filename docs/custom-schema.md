# Running your own deployment

What a fork sets to run this application on its own schema and under its own
namespaces. Everything is configuration, with one exception: the Welcome
guide's text, which lives in a single frontend file you replace.

For the conventions your schema must follow, see the data-model contract in
[data-model.md](data-model.md).

## Checklist

| What | Where | If you skip it |
|---|---|---|
| Your schema, with `sh:declare` for every prefix and `owl:versionInfo` | `SCHEMA_PATH` | the rfdb schema is loaded; without `sh:declare`, IRIs are not shortened to CURIEs; without a version, a startup warning |
| Base IRIs | `DATA_BASE_URI`, `SCHEMA_BASE_URI` | the services refuse to start (production) or use the rfdb namespaces (development) |
| Named graph | `DATA_GRAPH_URI` | records go into the rfdb graph name |
| Controlled vocabularies and sample data | `VOCAB_PATH`, `DATA_PATH`, `SEED_VOCAB_ON_STARTUP`, `SEED_TEST_DATA_ON_STARTUP` | the rfdb vocabularies are seeded |
| Read-only reference shapes | `READ_ONLY_SHAPES` | every shape is editable |
| Long-text fields | `LONG_TEXT_PREDICATES` | the rfdb list applies; your other text fields get one line |
| External identifiers shown as links | `EXTERNAL_ID_LINKS` | only VIAF and RISM IDs are linked |
| File uploads | one shape targeting `schema:DigitalDocument` in your schema (see [data-model.md](data-model.md#digital-documents)) | no upload field; the cleanup script refuses to run while file nodes or stored files exist |
| Project name | `PROJECT_NAME` (backends, editor); `VITE_APP_TITLE` (graph explorer, at build time) | pages and the editor say RossijskijFeatrDB; the explorer says RFDB Explorer |
| Welcome guide text | `curator-frontend/src/project/WelcomeGuideContent.jsx` | curators see the rfdb guide |
| Production domain | `RFDB_DOMAIN` in `.env.prod` | production refuses to start |
| Compose project name | `COMPOSE_PROJECT_NAME` in `.env` and `.env.prod`, e.g. `moviedb-dev` / `moviedb-prod` | Compose refuses to start; with the rfdb names, your stack and an rfdb stack on the same host would be treated as one |

Where settings go:

- **Development (Compose):** a repo-root `.env`. `docker-compose.yml` passes
  `SCHEMA_PATH`, `VOCAB_PATH`, `DATA_PATH`, `DATA_GRAPH_URI` and both base IRIs
  to the backends, falling back to the rfdb values when they are unset.
- **Production:** `.env.prod` (copy `.env.prod.example`). The same variables
  are read from it, with the same fallbacks, except that the base IRIs are
  required.
- **A backend run outside Compose:** the service's own `.env`. Both
  `.env.example` files describe every setting.

`READ_ONLY_SHAPES`, `LONG_TEXT_PREDICATES` and `EXTERNAL_ID_LINKS` are set once
at the top of each compose file (`x-read-only-shapes`, `x-long-text-predicates`,
`x-external-id-links`) and given to both backends from there. In a compose file
a literal `$` is written `$$`, so a template's `$1` appears there as `$$1`.

## Your schema

Put your schema file in `schema/`, next to the rfdb one, and point `SCHEMA_PATH`
at it, for example `SCHEMA_PATH=/app/schema/myschema.ttl`. Compose mounts the
whole `schema/` directory into both backends, so any file there is visible to
them. Vocabulary and sample-data files go in `data/` the same way.

Keep `schema/rfdb_schema.ttl` and the files in `data/`. The test suite tests the
application against that example schema, so it keeps passing whatever
`SCHEMA_PATH` the deployment uses.

That is why no test is marked as specific to the example schema, to be skipped
by a fork. Most test modules exercise the application through the example's
shapes and data (the form for a given shape, the validation of a given record,
the upload link a given class declares), so skipping them would leave a fork
with almost no tests. Keeping the example in place keeps the whole suite
meaningful. Tests for your own schema are yours to add.

## Base IRIs

Two settings name everything this deployment creates. Both backends must get
the same values.

| Setting | Names | Example value | Example IRI |
|---|---|---|---|
| `DATA_BASE_URI` | every record the curator mints, digital copies included | `https://example.org/rdf/data/` | `https://example.org/rdf/data/Place_4f3a9d12c07e` |
| `SCHEMA_BASE_URI` | the SHACL shapes of the loaded schema | `https://example.org/rdf/schema/` | `https://example.org/rdf/schema/PlaceShape` |

Rules:

1. Each must end with `/` or `#`. A service refuses to start otherwise.
2. `SCHEMA_BASE_URI` must be the namespace your schema's shapes already use. It
   tells the application where to find them and does not rename them.
3. **Change the host, keep the path.** Use `https://<your-host>/rdf/data/` and
   `https://<your-host>/rdf/schema/`. In production, `<your-host>` is
   `RFDB_DOMAIN`.

### Why the path must stay `/rdf/data/` and `/rdf/schema/`

An IRI is a host plus a path. Visiting `https://example.org/rdf/data/Place_1`
sends a request for the path `/rdf/data/Place_1` to that host. Two parts of the
stack only answer on fixed paths:

- the proxy forwards only `/rdf/*` to the reader (`proxy/Caddyfile`);
- the reader serves records on `/rdf/data/{name}` and shapes on
  `/rdf/schema/{name}`.

With any other path, for example `DATA_BASE_URI=https://example.org/kb/item/`,
records still save, validate, list and edit normally. What fails is everything
that fetches an IRI:

- visiting a record's IRI returns 404;
- links on the reader's HTML record pages point at paths nothing serves;
- uploaded PDFs do not open from the editor, because their
  `schema:contentUrl` is `<DATA_BASE_URI>File_…/content`.

Making these routes follow the settings is planned (see `TODO.md`, "Platform &
future").

## Long-text fields

SHACL has no way to say a field holds prose, so `LONG_TEXT_PREDICATES` lists the
predicates whose fields get a multi-line input, as a JSON array of full IRIs:

```
LONG_TEXT_PREDICATES=["http://purl.org/dc/terms/description", "http://www.w3.org/2004/02/skos/core#note"]
```

Set it in both compose files' `x-long-text-predicates`, or in each service's
`.env`. A field not listed still saves its full text; it is only shown on one
line.

## External identifiers

A schema can store an authority's identifier as plain text, the way Wikidata
does, rather than as a link. `EXTERNAL_ID_LINKS` maps each such predicate to the
URL of the authority's page, with `$1` where the identifier goes:

```
EXTERNAL_ID_LINKS={"http://www.wikidata.org/prop/direct/P214": "https://viaf.org/en/viaf/$1"}
```

Wikidata lists the template for most authorities as the property's formatter
URL (P1630). The form, the graph explorer and the reader's HTML page then show
the value as a link.

## The Welcome guide

On a curator's first visit the editor opens a guide to the data model and the
order in which records are created. Its text and its diagram are rfdb's, and
they live in one file,
[`curator-frontend/src/project/WelcomeGuideContent.jsx`](../curator-frontend/src/project/WelcomeGuideContent.jsx).
Replace that file with your own, keeping its export names: `guideTitle`,
`GuideIntro`, `GuideDiagram`, `GuideDiagramNote` and `guideSteps`. The dialog
around it needs no change.
