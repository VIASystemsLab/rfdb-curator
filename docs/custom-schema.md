# Running your own deployment

What a fork sets to run this application under its own namespaces. Everything
here is configuration: no step asks you to edit Python or JavaScript.

## Base IRIs

Two settings name everything this deployment creates. Both backends must get
the same values.

| Setting | Names | Example value | Example IRI |
|---|---|---|---|
| `DATA_BASE_URI` | every record the curator mints, digital copies included | `https://example.org/rdf/data/` | `https://example.org/rdf/data/Place_4f3a9d12` |
| `SCHEMA_BASE_URI` | the SHACL shapes of the loaded schema | `https://example.org/rdf/schema/` | `https://example.org/rdf/schema/PlaceShape` |

Where to set them:

- **Development:** a repo-root `.env`. `docker-compose.yml` passes both to
  both backends and falls back to the rfdb values when they are unset.
- **Production:** `.env.prod`. `docker-compose.prod.yml` refuses to start
  without them.
- **A backend run outside Compose:** the service's own `.env`. Both
  `.env.example` files describe each setting and show where it ends up.

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
- uploaded PDFs no longer open from the editor, because their
  `schema:contentUrl` is `<DATA_BASE_URI>File_…/content`.

Making these routes follow the settings is planned (see `TODO.md`, "Platform &
future").
