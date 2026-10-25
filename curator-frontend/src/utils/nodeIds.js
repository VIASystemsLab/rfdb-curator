/**
 * IRIs for nodes the editor creates inline (bridge/helper entries such as AgentRole).
 *
 * Minted the way the backend's `assign_entity_id` mints a record's IRI:
 * `DATA_BASE_URI`, the shape's name without "Shape", `_`, and 12 random hex
 * digits, e.g. `https://rosfeatr.eu/rdf/data/AgentRole_4f3a9d12c07e`.
 *
 * The base is hydrated at app startup via `hydrateDataBaseUri()`, from
 * `GET /api/v1/dataexplorer/meta/schema`. See App.jsx for the startup fetch.
 */

/** @type {string|null} DATA_BASE_URI. Populated at startup. */
let dataBaseUri = null

/**
 * Record the namespace new nodes are minted under.
 *
 * @param {string|null} uri - `dataBaseUri` from GET /api/v1/dataexplorer/meta/schema.
 */
export function hydrateDataBaseUri(uri) {
  dataBaseUri = uri || null
}

function randomHex12() {
  const bytes = crypto.getRandomValues(new Uint8Array(6))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * A new IRI for a node of the given shape.
 *
 * App.jsx does not open the editor until the base is loaded, so a missing base
 * is a bug: fail rather than send an IRI the backend did not expect.
 *
 * @param {string} shapeId - Full shape IRI, e.g. `https://rosfeatr.eu/rdf/schema/AgentRoleShape`.
 * @returns {string} Full IRI under DATA_BASE_URI.
 */
export function mintNodeId(shapeId) {
  if (!dataBaseUri) throw new Error('DATA_BASE_URI is not loaded; cannot name a new node.')
  const name = (shapeId || 'Entity').split(/[#/]/).pop().replaceAll('Shape', '')
  return `${dataBaseUri}${name}_${randomHex12()}`
}
