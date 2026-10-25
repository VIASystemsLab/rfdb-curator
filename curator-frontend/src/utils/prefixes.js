/**
 * Prefix compaction utility for display purposes.
 *
 * The backend returns raw full IRIs everywhere.  This module maps them to
 * their compact CURIE form (e.g. `https://rosfeatr.eu/rdf/data/Place_1` -> `rfdb:Place_1`)
 * for every place in the UI where a human-readable identifier is shown.
 *
 * The prefix map is hydrated at app startup via `hydratePrefixes()`, which
 * receives the response from `GET /api/v1/dataexplorer/meta/prefixes`.
 * The authoritative source is the rdflib graph parsed from the loaded schema on the backend.
 *
 * See App.jsx for the startup fetch and hydration call.
 */

/** @type {Record<string, string>} Maps CURIE prefix to full namespace URI. Populated at startup. */
export const prefixMap = {}

/**
 * Populate the prefix map from the backend response.
 * Should be called once at app startup before any IRI compaction is needed.
 *
 * @param {Record<string, string>} map - Flat object from GET /api/v1/dataexplorer/meta/prefixes.
 */
export function hydratePrefixes(map) {
  Object.assign(prefixMap, map)
}

/**
 * Compact a full IRI to its CURIE prefix form.
 *
 * Returns the value unchanged when:
 *   - `value` is falsy or not a string
 *   - the value already looks like a CURIE (contains `:` but no `http` scheme)
 *   - no prefix in prefixMap matches
 *
 * Literal strings (e.g. `"Libretto (1736)"@en`) are safe to pass through;
 * they will not match any namespace and are returned as-is.
 *
 * @param {string} value - A full IRI or any other string.
 * @returns {string} Compact CURIE (e.g. "rfdb:Place_abc") or the original value.
 */
export function compactIri(value) {
  if (!value || typeof value !== 'string') return value
  if (value.includes(':') && !value.startsWith('http://') && !value.startsWith('https://')) {
    return value
  }

  // Longest namespace first: of two nested namespaces the longer one wins.
  const entries = Object.entries(prefixMap).sort(([, a], [, b]) => b.length - a.length)
  for (const [prefix, ns] of entries) {
    if (value.startsWith(ns)) {
      return `${prefix}:${value.slice(ns.length)}`
    }
  }

  return value
}
