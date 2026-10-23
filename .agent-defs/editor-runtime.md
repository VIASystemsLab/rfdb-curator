# Runtime Diagnostics

## Purpose

Procedures for diagnosing, starting, and monitoring the standalone runtime stack (curator-backend + curator-frontend + graphexplorer-frontend + Oxigraph + Garage) via Docker Compose.

## Scope

- Focus on `curator-backend`, `curator-frontend`, `graphexplorer-frontend`, `oxigraph`, and `garage` services.
- `garage` is the S3-compatible object store for digital copies; curator-backend depends on it only when file-upload features are exercised.
- Include additional services only when they are direct dependencies.
- Use Docker Compose as the primary runtime interface.

## Preflight Checks

```bash
docker info
docker compose version
```

## Waiting for Readiness (Mandatory)

`oxigraph`, `garage` and both backends declare `healthcheck`s, and the services that depend on
them use `condition: service_healthy`. So **let Compose do the waiting** — do not hand-roll a
loop when this covers it:

```bash
docker compose up -d --wait --wait-timeout 180   # exits non-zero if a service never goes healthy
```

One caveat: `oxigraph`'s healthcheck is **liveness only** — the image is distroless, so the
check is `oxigraph --help`, which proves the process exists and says nothing about the HTTP
port. Healthy Oxigraph therefore does not mean queryable Oxigraph; `curator-backend`'s lifespan
polls `OxigraphClient.health()` for that reason.

When you genuinely must poll (a one-shot seed job, a route that has no healthcheck), the loop
must be **bounded** and **loud** — a hard attempt cap, the observed state printed every attempt,
and a loud failure at the cap:

```bash
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health || echo 000)
  echo "attempt $i: HTTP $code"
  [ "$code" = "200" ] && break
  sleep 2
done
[ "$code" = "200" ] || { echo "NOT READY after 30 attempts (last: $code)" >&2; exit 1; }
```

Never write `while true`, `until curl …; do sleep; done`, or any loop with no cap. **Silence is
not progress:** a command that prints nothing is not evidence that something is still starting,
and "it will be ready eventually" is not a termination condition. If the cap is hit, report the
last observed state rather than retrying at a longer interval.

Note that a 200 is not the same as ready-to-serve: `dataexplorer-backend` deliberately does not
wait for the store, so its `/health` answers while reporting `store: "down"`. Poll the thing you
actually need — for "is the data there", the triple count under *Health Verification* below.

## Compose Commands

Run from repository root. Lifecycle (`up` / `down` / `down -v`) is in
[build-commands.md](build-commands.md); these are the inspection commands.

```bash
docker compose ps
docker compose logs -f
docker compose restart <service>
docker compose exec <service> printenv <VAR>   # is the container seeing the config you think?
```

**The editor is behind the `full` profile.** A bare `docker compose up` starts the read-only
stack (Oxigraph, Garage, `dataexplorer-backend`, `graphexplorer-frontend`) and **no editor**.
Getting it requires `COMPOSE_PROFILES=full` in `.env`, which `scripts/env-init.sh` writes
but never adds to an existing `.env`, or `--profile full` per invocation.

Diagnose this before anything else when `:5173` refuses connections: `docker compose ps`
showing four containers rather than six is the whole answer, and it is not a broken editor.

## Startup Report

When starting the web app, report:

1. Startup result: started or blocked
2. Frontend URLs: `http://localhost:5173` (curator), `http://localhost` (graph explorer —
   published on host port 80; the Vite dev server itself still listens on :5174 inside the
   container)
3. Backend health URLs: `http://localhost:8000/health` (curator/writes),
   `http://localhost:8001/health` (dataexplorer/reads) — check **both**; the editor needs
   the writer, but every list and lookup it renders comes from the reader.

## Log Examination

```bash
docker compose logs --tail=100 curator-backend
docker compose logs --tail=100 dataexplorer-backend
docker compose logs --tail=100 curator-frontend
docker compose logs --tail=50
```

## Health Verification

```bash
# The two payloads differ by design: the writer reports {"status","oxigraph","seed"},
# the reader {"status","store"} with no seed report.
curl http://localhost:8000/health
curl http://localhost:8001/health
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173
curl -s -o /dev/null -w "%{http_code}" http://localhost

# Is the store actually queryable and loaded? Neither /health answers this.
curl -s -H 'Accept: application/sparql-results+json' \
  --data-urlencode 'query=SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }' \
  http://localhost:7878/query
```

POST queries with `--data-urlencode`; hand-built GET URLs break on `#`, `+` and `&` in IRIs.
A store that answers with `0` is up-but-empty — a seeding problem, not a connectivity one
([build-commands.md](build-commands.md) → "Seeding and Data Reset"). Distinguish the two before
reporting.

## Browser Verification (Mandatory)

Never open, launch, or drive a browser (Playwright, Puppeteer, headless Chrome, or similar) to verify frontend changes. Do not install or invoke browser-automation tooling for this purpose.

Instead: verify what is verifiable without a browser (API responses via `curl`, backend tests, lint/build), then tell the user the change is ready for them to test manually in their own browser and report back what they see.

## Previewing an inline SVG (e.g. the WelcomeGuide WEMI diagram)

To eyeball an SVG that lives inside a React component (no browser needed), render
it to PNG with macOS Quick Look and Read the PNG:

```bash
qlmanage -t -s 1000 -o <scratchpad-dir> diagram.svg   # writes diagram.svg.png
```

Two gotchas, both one-time:

- **Quick Look emits a square `s×s` thumbnail.** Portrait content fit to width
  overflows and the bottom is cropped. Fix: pad the standalone SVG's `viewBox`
  (and `width`/`height`) to roughly square/landscape so nothing is cut — this is
  preview-only; leave the real component's viewBox alone.
- **Component SVGs use CSS classes from a stylesheet**, so a standalone copy
  renders unstyled (lines have no stroke → invisible). Inline a `<style>` block
  with concrete values (copy from the component's `.css`), and add a dark
  background `<rect>` when the UI is dark-themed. Then paste the component's
  `<g>/<line>/<text>` markup verbatim.

No install needed (`qlmanage` ships with macOS). Do NOT rasterize via a headless
browser — same rule as Browser Verification above.

## Troubleshooting Tips

- **Frontend proxy errors (`/api` 502/504):** check which service owns the route first — a
  read failing means `dataexplorer-backend`, a save failing means `curator-backend`. The
  route ownership tables are in [docs/architecture.md](../docs/architecture.md#api-reference).
  Both apps now proxy everything the browser asks for, so a failing read *is* a proxy
  problem: the editor routes `/api/v1/curator` to the writer and `/api/v1/dataexplorer`
  plus `/rdf` to the reader, and the explorer sends `/api` and `/rdf` to the reader. Every
  request goes to the origin that served the page, so a read failure is an upstream or
  routing fault, not CORS. If you see reads aimed at `localhost:8001` in the Network tab
  while the app is served from anywhere else, a `VITE_READ_API_BASE` has been reintroduced
  somewhere — the dev stack sets none by design.
- **A 404 on a route you expect to exist:** most likely you asked the wrong service, and the
  path says which one — `/api/v1/curator/*` is the writer (:8000), `/api/v1/dataexplorer/*`
  and all of `/rdf/*` are the reader (:8001). `shapes` is the only route both serve, with an
  identical payload. A bare `/api/data`, `/api/shapes` etc. is not a route on either
  service and 404s everywhere.
- **The editor's sidebar is empty while the explorer works:** this is a **real bug**, even
  with `curator-backend` down. The editor fetches its shape catalogue
  from the *reader*, which serves the same flags the writer does, so a writer outage should
  cost you editing only — browsing, listing and searching keep working. If the sidebar is
  empty, check that `dataexplorer-backend` is up and that both services received
  `READ_ONLY_SHAPES` (`docker compose exec <svc> printenv READ_ONLY_SHAPES`).
- **Hot reload not picking up changes:** verify bind mounts in `docker-compose.yml`. Note
  that `rfdb-core` edits need the `--reload-dir /opt/rfdb-core` entry in the service
  command — without it the process keeps serving the previously imported library.
- **CORS errors:** verify `CORS_ORIGINS` on **both** services and the frontend API
  base/proxy settings. The reader must allow both frontend origins — `http://localhost:5173`
  and `http://localhost` (graphexplorer-frontend's published host port; 80 is http's default
  so the browser's Origin header omits it).
- **Oxigraph startup delays:** curator-backend polls for readiness before seeding.
  dataexplorer-backend deliberately does not wait — it reports `store: "down"` and
  recovers on its own, so a slow store shows up as empty reads, not a crash.
