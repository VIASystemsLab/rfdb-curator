/**
 * JSON-LD entity builder: converts react-hook-form state into a JSON-LD object.
 *
 * The form state produced by react-hook-form is a plain nested JS object keyed by
 * field path; dotted field names become nested objects (e.g.
 * `{"rdfs:label": {"__value": "Libretto", "__lang": "en"}}`).  This module
 * normalises that into valid JSON-LD that the backend expects.
 *
 * Field type → JSON-LD representation:
 *   - `lang-string`      → `{"@value": "...", "@language": "en"}`
 *   - `lang-string-list` → array of `{"@value": "...", "@language": "..."}`, keyed by full IRI
 *   - `year`             → `{"@value": "1736", "@type": "xsd:gYear"}`
 *   - `temporal`         → `{"@value": "...", "@type": ...}` typed by precision
 *                          (xsd:gYear / xsd:gYearMonth / xsd:date, else xsd:string)
 *   - `number`           → `{"@value": "42", "@type": "xsd:decimal"}`
 *   - `uri`              → `{"@id": "https://..."}`
 *   - `entity-search`    → `{"@id": "rfdb:Place_abc"}` (from AsyncSelect option)
 *   - `nested`           → inline node whose `@id` is an IRI under DATA_BASE_URI
 *                          minted by the frontend (utils/nodeIds.js, AgentRole pattern)
 *   - `file-list`        → inline schema:DigitalDocument nodes (FileField entries)
 *   - `text` / other     → plain string
 *
 * `buildJsonLdEntity()` is the only export; everything else is an internal helper.
 *
 * --- IMPORTANT: CREATE vs UPDATE ---
 * - If @id is missing, the backend will always create a new entity, so the
 *   payload for an edit must carry it (see `buildJsonLdEntity`).
 */
import { prefixMap } from './prefixes.js'

/**
 * Returns true for values the form should treat as "not filled in".
 * Handles null, undefined, empty strings, arrays of blanks, and objects
 * whose every property is blank.
 *
 * @param {*} value
 * @returns {boolean}
 */
function isBlank(value) {
  if (value == null) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0 || value.every((item) => isBlank(item))
  if (typeof value === 'object') return Object.values(value).every((item) => isBlank(item))
  return false
}

/**
 * Returns true when the field schema permits multiple values (maxCount is absent or > 1).
 *
 * @param {object} field - Property descriptor from /api/v1/curator/forms.
 * @returns {boolean}
 */
function shouldBeArray(field) {
  return field.maxCount !== 1
}

/**
 * Wrap a single value in an array when the field permits multiple values.
 * Leaves the value flat when maxCount === 1.
 */
function wrapMultiplicity(field, value) {
  if (value == null) return undefined
  return shouldBeArray(field) ? (Array.isArray(value) ? value : [value]) : value
}

/**
 * Normalise an AsyncSelect option or raw IRI string into a JSON-LD `@id` node.
 *
 * AsyncSelect options arrive as `{value: "<uri>", label: "..."}`.  Plain strings
 * (e.g. typed URIs) and objects already in `{"@id": ...}` form are also handled.
 */
function toEntityReference(entry) {
  if (isBlank(entry)) return undefined
  if (typeof entry === 'string') return { '@id': entry }
  if (entry?.value) return { '@id': entry.value }
  if (entry?.['@id']) return { '@id': entry['@id'] }
  return undefined
}

/**
 * Convert a scalar form value to its typed JSON-LD literal representation
 * based on the field schema type and datatype.
 *
 * Handles the following field types:
 *   - `lang-string`  → `{"@value": "...", "@language": "en"}`
 *   - `uri`          → `{"@id": "..."}`
 *   - `year`         → `{"@value": "...", "@type": "xsd:gYear"}`
 *   - `temporal`     → typed literal matching the date precision (gYear / gYearMonth / date / string)
 *   - `number`       → `{"@value": "...", "@type": field.datatype || "xsd:decimal"}`
 *   - other with datatype → `{"@value": "...", "@type": field.datatype}`
 *   - plain text     → string
 *
 * @param {object} field - Property descriptor from /api/v1/curator/forms.
 * @param {*}      value - Raw form value (string, object with __value/__lang, etc.).
 * @returns {*} JSON-LD node, typed literal object, or plain string; undefined when blank.
 */
function toTypedLiteral(field, value) {
  if (isBlank(value)) return undefined

  if (field.type === 'lang-string' || field.datatype === 'rdf:langString') {
    const literalValue = typeof value === 'object' ? value?.__value : value
    const language = typeof value === 'object' ? value?.__lang : undefined
    if (isBlank(literalValue)) return undefined
    const literal = { '@value': literalValue }
    if (language) literal['@language'] = language
    return literal
  }

  if (field.type === 'uri') {
    return { '@id': String(value).trim() }
  }

  if (field.type === 'year') {
    return { '@value': String(value).trim(), '@type': 'xsd:gYear' }
  }

  if (field.type === 'temporal') {
    const raw = String(value).trim()
    if (/^\d{4}$/.test(raw)) {
      return { '@value': raw, '@type': 'xsd:gYear' }
    }
    if (/^\d{4}-\d{2}$/.test(raw)) {
      return { '@value': raw, '@type': 'xsd:gYearMonth' }
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
      return { '@value': raw, '@type': 'xsd:date' }
    }
    return { '@value': raw, '@type': 'xsd:string' }
  }

  if (field.type === 'number') {
    const datatype = field.datatype || 'xsd:decimal'
    return { '@value': String(value).trim(), '@type': datatype }
  }

  if (field.datatype && field.datatype !== 'xsd:string') {
    return { '@value': String(value).trim(), '@type': field.datatype }
  }

  return String(value).trim()
}

/**
 * Collapse an AnonymousEntityEditor entry (which may carry react-hook-form
 * internal keys like `id` and `label`) into a clean JSON-LD node.
 * Blank `@id` values are left for `skolemize()` on the backend to resolve.
 */
function toNestedNode(entry) {
  if (!entry || typeof entry !== 'object') return undefined

  const result = {}
  if (entry['@id']) result['@id'] = entry['@id']
  if (entry['@type']) result['@type'] = entry['@type']

  for (const [key, value] of Object.entries(entry)) {
    if (key === 'id' || key === '@id' || key === '@type') continue
    if (isBlank(value)) continue
    const normalized = normalizeNestedValue(value)
    if (!isBlank(normalized)) {
      result[key] = normalized
    }
  }

  const predicateKeys = Object.keys(result).filter((key) => key !== '@id' && key !== '@type')
  return predicateKeys.length > 0 ? result : undefined
}

/**
 * Recursively normalise a value that appears inside a `nested` field entry.
 * Handles AsyncSelect options, existing `@id` references, language-tagged
 * literals (via the `__value`/`__lang` convention), and plain strings.
 */
function normalizeNestedValue(value) {
  if (isBlank(value)) return undefined

  if (Array.isArray(value)) {
    // Nested multi-value fields are normalized item-by-item and emptied
    // arrays are dropped so we never emit [] in JSON-LD payloads.
    const items = value.map((item) => normalizeNestedValue(item)).filter((item) => !isBlank(item))
    return items.length ? items : undefined
  }

  if (typeof value === 'object') {
    // react-select option shape: { value: '<iri>', label: '...' }.
    // For RDF we only keep the identifier.
    if (value.value) {
      return { '@id': value.value }
    }
    // Already-normalized entity reference.
    if (value['@id']) {
      return { '@id': value['@id'] }
    }
    // Language-tagged literal emitted by the lang-string control.
    if (value.__value) {
      const literal = { '@value': value.__value }
      if (value.__lang) literal['@language'] = value.__lang
      return literal
    }

    // Generic nested object: recursively normalize each key/value pair.
    const nested = {}
    for (const [k, v] of Object.entries(value)) {
      // UI-only label metadata (mostly from select widgets) must not become
      // an RDF predicate named "label" in the final JSON-LD.
      if (k === 'label') continue
      const normalized = normalizeNestedValue(v)
      if (!isBlank(normalized)) nested[k] = normalized
    }
    return Object.keys(nested).length ? nested : undefined
  }

  // Plain scalar fallback (already a literal-like value).
  if (typeof value === 'string') return value.trim()
  return value
}

/**
 * Convert a FileField entry (staged-upload response or edit-mode hydration)
 * into an inline schema:DigitalDocument node. The values are a UI prefill —
 * the backend re-derives (staged) or ignores (registered) them at write time —
 * but they must be present and typed so pre-write SHACL validation conforms.
 */
function toDigitalCopyNode(entry) {
  const id = entry?.['@id'] ?? entry?.id
  if (!id) return undefined
  const node = {
    '@id': id,
    '@type': 'schema:DigitalDocument',
    'schema:name': String(entry.name ?? ''),
    'schema:encodingFormat': 'application/pdf',
    'schema:contentUrl': { '@value': String(entry.contentUrl ?? ''), '@type': 'xsd:anyURI' },
    'schema:contentSize': { '@value': String(entry.contentSize ?? 0), '@type': 'xsd:integer' },
    'schema:sha256': String(entry.sha256 ?? ''),
  }
  if (entry.numberOfPages != null) {
    node['schema:numberOfPages'] = { '@value': String(entry.numberOfPages), '@type': 'xsd:integer' }
  }
  return node
}

/**
 * Dispatch a raw form field value to the correct normalisation function based
 * on the field schema `type`.
 *
 * @param {object} field - Property descriptor from /api/v1/curator/forms.
 * @param {*}      value - Raw form value.
 * @returns {*} Normalised JSON-LD value, or undefined when blank.
 */
function normalizeFieldValue(field, value) {
  if (isBlank(value)) return undefined

  if (field.type === 'file-list') {
    const entries = Array.isArray(value) ? value : [value]
    const items = entries.map((entry) => toDigitalCopyNode(entry)).filter(Boolean)
    return items.length ? items : undefined
  }

  if (field.type === 'uri' && Array.isArray(value)) {
    const items = value.map((entry) => toTypedLiteral(field, entry)).filter((entry) => !isBlank(entry))
    return items.length ? items : undefined
  }

  if (field.type === 'entity-search') {
    if (Array.isArray(value)) {
      const items = value.map((entry) => toEntityReference(entry)).filter(Boolean)
      return items.length ? items : undefined
    }
    return toEntityReference(value)
  }

  if (field.type === 'nested') {
    if (Array.isArray(value)) {
      const items = value.map((entry) => toNestedNode(entry)).filter(Boolean)
      return items.length ? items : undefined
    }
    return toNestedNode(value)
  }

  return toTypedLiteral(field, value)
}

/**
 * Build a complete JSON-LD entity object from a react-hook-form submission.
 *
 * @param {object} shape  - Shape descriptor from `/api/v1/curator/forms` (has `targetClass`).
 * @param {Array}  fields - Field descriptors from `/api/v1/curator/forms` (has `path`, `type`, etc.).
 * @param {object} rawFormData - The raw `formData` object from `handleSubmit`.
 * @returns {object} A JSON-LD object ready to POST to `/api/v1/curator/entities`.
 */
export function buildJsonLdEntity(shape, fields, rawFormData) {
  const entity = {
    '@context': prefixMap,
  }

  // --- Ensure @id is included if present in form data ---
  // This is critical for updates: if @id is missing, the backend will always create a new entity.
  if (rawFormData['@id']) {
    entity['@id'] = rawFormData['@id']
  }

  if (shape?.targetClass) {
    const additionalTypes = shape.additionalTypes ?? []
    if (shape.typeOptions?.length > 0 && rawFormData.__typeChoice) {
      // Polymorphic shape (shape-level sh:or/sh:class, e.g. ContributorShape):
      // targetClass alone never satisfies the sh:or constraint, so the
      // user-picked concrete type is asserted alongside it.
      entity['@type'] = [shape.targetClass, rawFormData.__typeChoice]
    } else {
      entity['@type'] =
        additionalTypes.length > 0 ? [shape.targetClass, ...additionalTypes] : shape.targetClass
    }
  }

  for (const field of fields) {
    const rawValue = rawFormData[field.path]
    // --- LangStringList: uses field.pathUri (full IRI) as the JSON-LD key ---
    // All other field types use field.path (CURIE form, e.g. "rdfs:label") because
    // the prefixMap @context block resolves CURIEs to full IRIs on the backend.
    // lang-string-list uses field.pathUri (the already-expanded IRI) instead because
    // the backend's JSON-LD parser requires the full IRI for multi-valued language-tagged
    // properties when the array is emitted directly without a CURIE key.
    // If you add a new field type and copy from here, use field.path (not field.pathUri)
    // unless you have a specific reason to bypass @context resolution.
    if (field.type === 'lang-string-list') {
      const items = (rawValue ?? [])
        .filter((item) => item.__value && item.__value.trim() !== '')
        .map((item) => ({ '@value': item.__value, '@language': item.__lang }))
      if (items.length > 0) {
        entity[field.pathUri] = items
      }
      continue
    }
    const normalized = normalizeFieldValue(field, rawValue)
    const finalValue = wrapMultiplicity(field, normalized)
    if (finalValue !== undefined) {
      entity[field.path] = finalValue
    }
  }

  return entity
}
