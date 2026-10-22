/**
 * Centralised API client used by all React components.
 *
 * Two backends, so two bases:
 *   - `BASE_URL` → curator-backend (:8000), the only service that writes.
 *   - `READ_BASE` → dataexplorer-backend (:8001), which answers every read.
 *
 * Routes are namespaced by owning service (D8): `/api/v1/curator/…` and
 * `/api/v1/dataexplorer/…`, plus the reader's unversioned `/rdf/…` data space for
 * anything with a persisted identifier. Pointing a call at the wrong base now
 * fails with a clean 404 rather than a 405 from a path that exists on both
 * services for different methods.
 *
 * Both bases are **relative in dev and in production**, deliberately: every
 * request goes to the origin that served the page, so nothing browser-facing
 * names a host and one config runs on localhost, on an IP, through an SSH
 * tunnel, or behind a domain. Routing lives where hostnames are stable — the
 * Vite dev server's proxy (vite.config.js) and proxy/Caddyfile in prod, which
 * split the identical prefixes.
 *
 * An absolute base breaks remote dev hosts, because the browser would resolve
 * reads against its own machine. Set a non-empty base only for a build whose
 * reader genuinely sits on another origin, and add a matching CORS_ORIGINS
 * entry on that reader.
 *
 * Fetch methods return the unwrapped `data` field of the axios response, so
 * callers work directly with the JSON payload. `deleteEntity` returns the full
 * axios response, and the URL helpers return strings without making a request.
 */
import axios from 'axios'

const BASE_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_BASE ?? '')
const READ_BASE = import.meta.env.DEV ? '' : (import.meta.env.VITE_READ_API_BASE ?? '')

/**
 * Cross-app deep link to the standalone Explorer. Unlike the two bases above,
 * this one cannot be relative in dev: the Explorer is a *different origin*
 * there (published on :80 while this app is on :5173), where production serves
 * it as a path on the one domain. So derive it from the browser's own hostname
 * instead of pinning a host. Unset outside dev → '' and callers render no link.
 *
 * devnote: the derived form assumes the dev Explorer is on the default port.
 * Set VITE_EXPLORER_BASE explicitly if you republish it somewhere else.
 */
const EXPLORER_BASE =
  import.meta.env.VITE_EXPLORER_BASE ??
  (import.meta.env.DEV ? `${window.location.protocol}//${window.location.hostname}` : '')

/** Writer surface. */
const WRITE_API = '/api/v1/curator'
/** Reader's operational surface. */
const READ_API = '/api/v1/dataexplorer'

const http = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

/**
 * Reads go to dataexplorer-backend: via the /api/v1/dataexplorer prefix rule of
 * the dev proxy, or the edge proxy in production.
 */
const readHttp = axios.create({
  baseURL: READ_BASE,
  headers: { 'Content-Type': 'application/json' },
})

export const apiClient = {
  /**
   * Fetch all SHACL NodeShapes.  Called once on app mount to populate the sidebar.
   *
   * On the **reader**, and that is the fix for C20 rather than an optimisation.
   * This used to be pinned to the writer because only the writer stamped the
   * `readOnly` flags the editor needs to disable protected shapes — so with the
   * writer down the editor had no shape list and rendered an empty sidebar. Per
   * D11 both services now build the identical stamped catalogue from one shared
   * implementation, so the editor can start, browse and search with the write tier
   * stopped. Only editing itself requires the writer.
   */
  getShapes: () => readHttp.get(`${READ_API}/shapes`).then((r) => r.data),

  /**
   * Fetch form field definitions for a single shape. Writer-only, deliberately:
   * form fields exist to drive an editing form, so needing the writer to open one
   * is not a limitation.
   */
  getFormSchema: (shapeId) =>
    http.get(`${WRITE_API}/forms`, { params: { shapeId } }).then((r) => r.data),

  /** List stored entities for a shape with optional text search and pagination. */
  listData: (shapeId, { q = '', limit = 50, offset = 0 } = {}) =>
    readHttp.get(`${READ_API}/entities`, { params: { shapeId, q, limit, offset } }).then((r) => r.data),

  /** Fetch per-shape record counts for the sidebar count pills. */
  getDataCounts: () => readHttp.get(`${READ_API}/entities/counts`).then((r) => r.data),

  /**
   * Fetch all triples for a single entity (used by ValidationPanel inspector).
   *
   * The IRI goes in `?id=`, never in the path. Axios encodes params, so a single
   * `encodeURIComponent` is not needed here — and a path-encoded IRI would need
   * double-encoding to survive the round trip anyway.
   */
  getEntity: (entityId) =>
    readHttp.get(`${READ_API}/entities/get`, { params: { id: entityId } }).then((r) => r.data),

  /** Create or update an entity.  Returns success flag + validation report. */
  createEntity: (payload) => http.post(`${WRITE_API}/entities`, payload).then((r) => r.data),

  /**
   * Autocomplete search for entities of a given shape (relation field dropdown).
   * `query` may be empty to pre-populate the dropdown on field focus.
   */
  searchEntities: (shape, query, limit = 50) =>
    readHttp
      .get(`${READ_API}/entities/search`, { params: { shape, query, limit } })
      .then((r) => r.data),

  /** Delete an entity by IRI. Pass shapeId to enable per-shape write protection on the backend. */
  deleteEntity: (entityId, shapeId = '') =>
    http.delete(`${WRITE_API}/entities`, {
      params: shapeId ? { id: entityId, shapeId } : { id: entityId },
    }),

  /**
   * Fetch the authoritative prefix-to-namespace map from the backend.
   * Returns a plain object keyed by prefix (e.g. { "rfdb": "https://…", … }).
   * Called once at app startup to hydrate utils/prefixes.js and utils/jsonld.js.
   */
  getPrefixes: () => readHttp.get(`${READ_API}/meta/prefixes`).then((r) => r.data.prefixes),

  /**
   * Fetch what the loaded schema says about itself and the deployment's base IRIs:
   * { version, dataBaseUri, schemaBaseUri }. Called once at app startup.
   */
  getSchemaInfo: () => readHttp.get(`${READ_API}/meta/schema`).then((r) => r.data),

  /**
   * Stage a PDF (upload-first flow). Returns the prefilled digital-copy node
   * ({id, fileId, name, contentUrl, contentSize, sha256, numberOfPages}) that
   * the FileField appends to form state; it becomes a bridge node in the
   * JSON-LD payload on submit. `file` is a browser File object.
   *
   * The returned `contentUrl` is the *staged* path on this service — pass it to
   * `resolveFileUrl` rather than assuming a base.
   */
  stageFile: (file) => {
    const form = new FormData()
    form.append('file', file)
    return http
      .post(`${WRITE_API}/files/staged`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data)
  },

  /**
   * Fetchable URL for a digital copy's bytes, from the node's `schema:contentUrl`.
   *
   * Two shapes arrive here, and the difference is not cosmetic (D7 + D9):
   *
   *   relative  `/api/v1/curator/files/staged/{id}`   staged working state, served
   *                                                   by the writer that took the
   *                                                   upload → resolve on BASE_URL
   *   absolute  `https://rosfeatr.eu/rdf/data/{id}/content`
   *                                                   a published cool URI → see
   *                                                   the re-basing note below
   *
   * The absolute form is an **identifier**, not necessarily a reachable URL here:
   * it names the resource on the production host, which is not where a dev reader
   * lives. Fetching it as-is would send dev traffic to the public internet. So we
   * keep its path and resolve it against whichever origin actually serves reads in
   * this deployment — a no-op in production, where READ_BASE *is* that host.
   *
   * Never resolve a staged path against READ_BASE: the reader 404s anything no
   * entity references yet, by design.
   */
  resolveFileUrl: (contentUrl) => {
    if (!contentUrl) return ''
    if (!/^https?:\/\//i.test(contentUrl)) return `${BASE_URL}${contentUrl}`
    try {
      return `${READ_BASE}${new URL(contentUrl).pathname}`
    } catch {
      return contentUrl // not a parseable URL; hand it back rather than guessing
    }
  },

  /**
   * Download URL for a **published** copy known only by file id.
   * Prefer `resolveFileUrl(entry.contentUrl)` when the node is at hand — it
   * handles the staged case too.
   */
  fileDownloadUrl: (fileId) => `${READ_BASE}/rdf/data/${encodeURIComponent(fileId)}/content`,

  /**
   * Browser URL for an entity's human-readable HTML description page —
   * the same `/rdf/data/{id}` route content-negotiated for a browser instead
   * of a client that sent an RDF `Accept` header. `iri` is always absolute
   * (an entity id), so only the re-basing `resolveFileUrl` does for absolute
   * `contentUrl`s applies; there is no staged-relative case here.
   */
  entityPageUrl: (iri) => {
    try {
      return `${READ_BASE}${new URL(iri).pathname}`
    } catch {
      return null
    }
  },

  /**
   * Deep link into the Explorer for one record, or '' when this deployment has
   * no Explorer configured — callers render the link only when it is truthy.
   */
  explorerRecordUrl: (id) =>
    EXPLORER_BASE ? `${EXPLORER_BASE}/?id=${encodeURIComponent(id)}` : '',

  /** Digital-copy storage stats (staged/registered/orphans) for the Data Context Panel. */
  getFileStats: () => readHttp.get(`${READ_API}/meta/files`).then((r) => r.data),

  /**
   * Fetch the runtime graph context for the read-only Data Context Panel:
   * active graph URI, named graphs with triple counts, a store-wide total,
   * and advisory config warnings. Store-wide (unscoped) — fetched lazily on
   * panel open, not at startup.
   */
  getGraphs: () => readHttp.get(`${READ_API}/meta/graphs`).then((r) => r.data),
}
