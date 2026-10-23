/**
 * Links for external identifiers stored as bare text (a VIAF ID "293925180",
 * a RISM ID "sources/990052516").
 *
 * The template comes from the field descriptor's `linkTemplate`, which the
 * backend fills from the EXTERNAL_ID_LINKS setting; `$1` marks where the
 * identifier goes. Each path segment is percent-encoded and `/` is kept, as the
 * backend's rfdb_core.external_ids does.
 */
export function externalIdUrl(template, identifier) {
  const encoded = String(identifier).trim().split('/').map(encodeURIComponent).join('/')
  return template.replace('$1', encoded)
}
