/** SHACL-driven form: loads schema metadata, maps records to form state, and submits JSON-LD. */
import { useEffect, useRef, useState } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { apiClient } from '../api/client.js'
import { buildJsonLdEntity } from '../utils/jsonld.js'
import { compactIri } from '../utils/prefixes.js'

import FormField from './FormField.jsx'
import StyledSelect from './StyledSelect.jsx'
import './ShapeForm.css'

const RDF_TYPE_URI = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'

/**
 * Resolve which `typeOptions` value (e.g. "foaf:Person") a record's actual
 * `rdf:type` triples match, for pre-selecting the type selector on edit.
 * Returns '' when the record has none of the declared alternatives (e.g. a
 * brand new shape whose data predates this constraint).
 */
function resolveTypeChoice(flatTriples, typeOptions) {
  const raw = flatTriples['rdf:type']
  const values = Array.isArray(raw) ? raw : raw ? [raw] : []
  const types = values.map((t) => compactIri(t.object))
  const match = typeOptions.find((opt) => types.includes(opt.value))
  return match?.value ?? ''
}

/**
 * Convert backend triples for one predicate into the value shape expected by a field widget.
 */
function tripleToFieldValue(val, fieldType, languageTagPolicy, maxCount = 1) {
  const triple = Array.isArray(val) ? val[0] : val

  if (fieldType === 'lang-string-list') {
    const values = Array.isArray(val) ? val : val ? [val] : []
    const mapped = values.map((t) => ({
      __value: t.object ?? '',
      __lang: t.language ?? 'en',
    }))
    // Never show a fully blank list widget on edit.
    return mapped.length > 0 ? mapped : [{ __value: '', __lang: 'en' }]
  }

  if (fieldType === 'lang-string') {
    // react-hook-form controllers for lang strings bind to path.__value and path.__lang.
    // The form state must therefore store a nested object at formData[path].
    const defaultLang = languageTagPolicy === 'required' ? 'en' : ''
    if (
      triple &&
      typeof triple === 'object' &&
      'objectType' in triple &&
      triple.objectType === 'literal'
    ) {
      return { __value: triple.object, __lang: triple.language ?? defaultLang }
    }
    return { __value: triple ? triple.object || String(triple) : '', __lang: defaultLang }
  }

  if (fieldType === 'uri') {
    const isMulti = maxCount !== 1
    const values = Array.isArray(val) ? val : val ? [val] : []
    const mapped = values
      .map((entry) => {
        if (entry && typeof entry === 'object') {
          if (entry.objectType === 'iri') return entry.object
          if (entry['@id']) return entry['@id']
          if (typeof entry.object === 'string') return entry.object
          return ''
        }
        return typeof entry === 'string' ? entry : ''
      })
      .filter((entry) => entry && String(entry).trim() !== '')
    return isMulti ? mapped : (mapped[0] ?? '')
  }

  if (fieldType === 'temporal' || fieldType === 'year' || fieldType === 'number') {
    if (triple && typeof triple === 'object' && triple['@value']) return triple['@value']
    if (triple && typeof triple === 'object' && triple.object) return triple.object
    if (typeof triple === 'string') return triple
    return ''
  }

  // Generic fallback for plain text-like scalar fields. Backend triples
  // commonly arrive as objects with `.object` for literal values; map those
  // to scalar strings so inputs never render "[object Object]".
  if (triple && typeof triple === 'object') {
    if (triple['@value']) return triple['@value']
    if (typeof triple.object === 'string') return triple.object
    if (typeof triple['@id'] === 'string') return triple['@id']
    return ''
  }
  return triple ?? ''
}

function tripleToFieldScalarValue(val, fieldType, languageTagPolicy) {
  return tripleToFieldValue(val, fieldType, languageTagPolicy, 1)
}

export default function ShapeForm({
  shape,
  allShapes,
  record,
  draftKey,
  draftValue,
  onDraftChange,
  onValidation,
  onSaved,
  onReset,
}) {
  const [formSchema, setFormSchema] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  // Bumped by the Reset button to force the hydration effect to re-seed a blank form
  // after its draft has been cleared (a create-mode reset otherwise leaves no state
  // change for the effect to react to).
  const [resetNonce, setResetNonce] = useState(0)
  // Which record the edit form has finished loading, and the connection fields
  // whose bridge nodes failed to load. Until the current record is loaded with
  // no failures the form cannot be saved: isFieldDirty compares the loaded
  // bridge IRIs with the form's, so a missing card would read as a removal and
  // the backend would delete that connection.
  const [hydration, setHydration] = useState({ record: null, failed: [] })

  const { register, handleSubmit, reset, control, watch, formState } = useForm()

  // Latest draft, read at hydration time without making the hydration effect depend
  // on draftValue — otherwise every keystroke (which updates draftValue upstream)
  // would re-run reset() and clobber the user's in-progress edits.
  const draftValueRef = useRef(draftValue)
  draftValueRef.current = draftValue

  // Suppresses draft publishing while we programmatically reset() during hydration,
  // so restoring a draft (or seeding defaults) never echoes back up to the parent.
  const hydratingRef = useRef(false)

  /**
   * reset() wrapper that flags the surrounding hydration so the watch subscription
   * ignores the resulting change event (see the publish effect below).
   */
  function hydrateReset(values) {
    hydratingRef.current = true
    reset(values)
    hydratingRef.current = false
  }

  function formatApiError(err) {
    const detail = err?.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (typeof item === 'string') return item
          if (item?.msg) return item.msg
          return null
        })
        .filter(Boolean)
      if (parts.length > 0) return parts.join('; ')
    }
    if (detail && typeof detail === 'object') {
      try {
        return JSON.stringify(detail)
      } catch {
        // fallback handled below
      }
    }
    return err?.message || 'Save failed'
  }

  function collectErrorPaths(node, prefix = '') {
    if (!node || typeof node !== 'object') return []
    if (node.type) return [prefix]

    const paths = []
    for (const [key, value] of Object.entries(node)) {
      const nextPrefix = prefix ? `${prefix}.${key}` : key
      paths.push(...collectErrorPaths(value, nextPrefix))
    }
    return paths
  }

  useEffect(() => {
    if (!shape) return
    setFormSchema(null)

    // Ignore stale in-flight responses when the selected shape changes quickly.
    let ignore = false

    apiClient
      .getFormSchema(shape.id)
      .then((res) => {
        if (ignore) return
        setFormSchema(res)
      })
      .catch((err) => {
        if (ignore) return
        console.error(err)
      })

    return () => {
      ignore = true
    }
  }, [shape])

  // Flatten triples into a predicate-keyed map while preserving full triple metadata.
  function triplesToFlatObject(triples) {
    if (!Array.isArray(triples)) return {}
    const out = {}
    for (const triple of triples) {
      const key = compactIri(triple.predicate)
      if (out[key]) {
        if (Array.isArray(out[key])) {
          out[key].push(triple)
        } else {
          out[key] = [out[key], triple]
        }
      } else {
        out[key] = triple
      }
    }
    return out
  }

  /**
   * Resolve a human-readable label for an entity IRI by querying the search API.
   * Falls back to the IRI itself if no label is found or the request fails.
   * Used to populate entity-search fields with proper labels on form load.
   */
  async function resolveEntityLabel(iri, field) {
    try {
      // Prefer nestedShape (shape ID like "PlaceShape") over nodeClass (class IRI).
      // This matches the same priority used by EntitySearch.jsx loadOptions.
      const targetShape = field?.nestedShape ?? field?.nodeClass ?? ''
      const results = await apiClient.searchEntities(targetShape, iri, 5)
      // Backend returns {uri, label} — match against r.uri only.
      const match = results?.find((r) => r.uri === iri)
      if (match) {
        return { value: match.uri, label: match.label || iri }
      }
    } catch {
      // fall through to IRI fallback
    }
    return { value: iri, label: iri }
  }

  /** Map a loaded record into react-hook-form state for the active schema fields. */
  function mapRecordToFormData(record, fields) {
    if (!record || !fields) return {}
    const flat = record.triples ? triplesToFlatObject(record.triples) : record
    const formData = {}

    // Always carry @id forward so the backend knows this is an update
    if (record && record.id) {
      formData['@id'] = record.id
    }

    for (const field of fields) {
      const val = flat[field.path]

      // --- Multi-value language-tagged list (e.g. skos:altLabel) ---
      if (field.type === 'lang-string-list') {
        formData[field.path] = tripleToFieldScalarValue(val, field.type, field.languageTagPolicy)
        continue
      }

      // --- Single language-tagged string (e.g. rdfs:label) ---
      if (field.type === 'lang-string') {
        formData[field.path] = tripleToFieldScalarValue(val, field.type, field.languageTagPolicy)
        continue
      }

      // --- Entity search ---
      // Initialised to null here; resolved asynchronously in useEffect
      // to obtain a proper {value, label} object the async-select can display.
      if (field.type === 'entity-search') {
        formData[field.path] = null
      }

      // --- URI input ---
      else if (field.type === 'uri') {
        formData[field.path] = tripleToFieldValue(
          val,
          field.type,
          field.languageTagPolicy,
          field.maxCount
        )
      }

      // --- Nested bridge entity ---
      // Not populated here — handled asynchronously in the useEffect below
      // by fetching each bridge node's own triples from the backend.
      // Initialised to empty array so useFieldArray starts in a clean state
      // while the async fetch is in flight.
      else if (field.type === 'nested') {
        formData[field.path] = []
      }

      // --- Digital copies (file-list) ---
      // Same pattern as nested: entries are hydrated asynchronously from the
      // linked schema:DigitalDocument nodes' own triples.
      else if (field.type === 'file-list') {
        formData[field.path] = []
      }

      // --- Temporal (xsd:date / xsd:gYear / xsd:gYearMonth) ---
      else if (field.type === 'temporal') {
        formData[field.path] = tripleToFieldScalarValue(val, field.type, field.languageTagPolicy)
      }

      // --- Year and Number ---
      else if (field.type === 'year' || field.type === 'number') {
        formData[field.path] = tripleToFieldScalarValue(val, field.type, field.languageTagPolicy)
      }

      // --- Generic fallback for text-like fields ---
      else {
        formData[field.path] = tripleToFieldScalarValue(val, field.type, field.languageTagPolicy)
      }
    }

    return formData
  }

  useEffect(() => {
    if (!formSchema) return

    setSubmitError(null)

    // Only new (create) forms restore a draft. Editing an existing record always
    // reloads from that record — a draft can never carry an @id, so it can never
    // silently turn a create into an update of some previously-edited entity.
    // Read via ref so this effect does not depend on draftValue (which changes on
    // every keystroke and would otherwise re-run reset() and wipe in-progress edits).
    const savedDraft = draftValueRef.current
    if (savedDraft && !record?.id) {
      const restored = { ...savedDraft }
      delete restored['@id'] // defensive: a create form never carries an identity
      hydrateReset(restored)
      return
    }

    // Ignore stale async mapping results if record/shape changes mid-flight.
    let ignore = false

    if (!record || !record.id) {
      // New/blank record. Seed EVERY field with its empty value (not just the
      // list widgets) so reset() clears all fields. This matters after editing:
      // react-hook-form's defaultValues are then the edited record's values, so
      // a partial reset would leave omitted fields (and a stale @id) populated —
      // the "edited values linger after save" bug.
      const defaults = { '@id': undefined }
      for (const field of formSchema.fields) {
        if (field.type === 'lang-string-list') {
          defaults[field.path] = [{ __value: '', __lang: 'en' }]
        } else if (field.type === 'lang-string') {
          defaults[field.path] = {
            __value: '',
            __lang: field.languageTagPolicy === 'required' ? 'en' : '',
          }
        } else if (field.type === 'uri') {
          defaults[field.path] = field.maxCount !== 1 ? [''] : ''
        } else if (field.type === 'entity-search') {
          defaults[field.path] = null
        } else if (field.type === 'nested' || field.type === 'file-list') {
          defaults[field.path] = []
        } else {
          // text, number, year, temporal, enum, default
          defaults[field.path] = ''
        }
      }
      if (formSchema.shape.typeOptions?.length > 0) {
        defaults.__typeChoice = ''
      }
      hydrateReset(defaults)
      return () => {
        ignore = true
      }
    }

    // Editing existing record:
    // Step 1 — map all scalar fields synchronously so the form is not blank
    //           while async fetches are in flight.
    setHydration({ record: null, failed: [] })
    const mapped = mapRecordToFormData(record, formSchema.fields)
    const flat = triplesToFlatObject(record.triples)
    const failedNested = []

    if (formSchema.shape.typeOptions?.length > 0) {
      mapped.__typeChoice = resolveTypeChoice(flat, formSchema.shape.typeOptions)
    }

    const nestedFields = formSchema.fields.filter((f) => f.type === 'nested')
    const entitySearchFields = formSchema.fields.filter((f) => f.type === 'entity-search')
    const fileFields = formSchema.fields.filter((f) => f.type === 'file-list')

    if (nestedFields.length === 0 && entitySearchFields.length === 0 && fileFields.length === 0) {
      // Nothing async needed — reset straight away
      hydrateReset(mapped)
      setHydration({ record, failed: [] })
      return () => {
        ignore = true
      }
    }

    // Step 2 — resolve nested bridge entities and entity-search labels in parallel.
    const nestedPromises = nestedFields.map(async (field) => {
      const val = flat[field.path]

      // Collect all bridge IRIs linked via this predicate (may be multiple)
      const rawVals = Array.isArray(val) ? val : val ? [val] : []
      const iris = rawVals.map((t) => (t?.objectType === 'iri' ? t.object : null)).filter(Boolean)

      if (iris.length === 0) {
        mapped[field.path] = []
        return
      }

      // Resolve the nested shape descriptor so we know which properties to map
      const resolvedShape = allShapes?.find((s) => s.id === field.nestedShape)

      const entries = await Promise.all(
        iris.map(async (iri) => {
          try {
            const entity = await apiClient.getEntity(iri)
            const bridgeFlat = triplesToFlatObject(entity.triples)

            // Start with the IRI so AnonymousEntityEditor can identify the node
            const entry = {
              '@id': iri,
              // Keep rdf:type in form state so nested nodes satisfy sh:hasValue rdf:type constraints.
              '@type': resolvedShape?.targetClass ?? '',
            }

            if (resolvedShape) {
              for (const prop of resolvedShape.properties) {
                if (prop.path === 'rdf:type') continue

                const propVal = bridgeFlat[prop.path]
                if (!propVal) continue

                if (prop.type === 'entity-search') {
                  // Resolve label for entity-search props inside bridge entities too
                  const t = Array.isArray(propVal) ? propVal[0] : propVal
                  const iriVal = t?.objectType === 'iri' ? t.object : String(t)
                  entry[prop.path] = await resolveEntityLabel(iriVal, prop)
                } else {
                  // Reuse the same triple-to-widget mapping as top-level fields.
                  entry[prop.path] = tripleToFieldValue(propVal, prop.type, prop.languageTagPolicy)
                }
              }
            }

            return entry
          } catch {
            // Skip the card so the rest of the form still loads, but record the
            // field: saving now would delete this connection (see `hydration`).
            if (!failedNested.includes(field.name)) failedNested.push(field.name)
            return null
          }
        })
      )

      mapped[field.path] = entries.filter(Boolean)
    })

    // Step 2b — hydrate file-list entries from the linked digital-copy nodes.
    // Same fetch pattern as nested bridge entities; each entry becomes the
    // plain object shape FileField renders and jsonld.js re-emits on submit.
    const filePromises = fileFields.map(async (field) => {
      const val = flat[field.path]
      const rawVals = Array.isArray(val) ? val : val ? [val] : []
      const iris = rawVals.map((t) => (t?.objectType === 'iri' ? t.object : null)).filter(Boolean)

      const entries = await Promise.all(
        iris.map(async (iri) => {
          try {
            const entity = await apiClient.getEntity(iri)
            const fileFlat = triplesToFlatObject(entity.triples)
            const scalar = (key) => {
              const t = fileFlat[key]
              const first = Array.isArray(t) ? t[0] : t
              return first?.object
            }
            const fileId = iri.split('/').pop()
            const pages = scalar('schema:numberOfPages')
            return {
              '@id': iri,
              fileId,
              name: scalar('schema:name') ?? fileId,
              contentUrl: scalar('schema:contentUrl') ?? '',
              contentSize: Number(scalar('schema:contentSize') ?? 0),
              sha256: scalar('schema:sha256') ?? '',
              numberOfPages: pages != null ? Number(pages) : null,
            }
          } catch {
            // Skip a broken node rather than blocking the whole form load.
            return null
          }
        })
      )
      mapped[field.path] = entries.filter(Boolean)
    })

    // Step 3 — resolve labels for top-level entity-search fields
    const entitySearchPromises = entitySearchFields.map(async (field) => {
      const val = flat[field.path]
      const triple = Array.isArray(val) ? val[0] : val
      if (!triple) {
        mapped[field.path] = null
        return
      }

      // Extract the raw IRI from the triple
      let iri = null
      if (triple?.objectType === 'iri') {
        iri = triple.object
      } else if (triple?.['@id']) {
        iri = triple['@id']
      } else if (typeof triple?.object === 'string') {
        iri = triple.object
      } else if (typeof triple === 'string') {
        iri = triple
      }

      if (!iri) {
        mapped[field.path] = null
        return
      }

      // Resolve to {value, label} using the search API
      mapped[field.path] = await resolveEntityLabel(iri, field)
    })

    Promise.all([...nestedPromises, ...filePromises, ...entitySearchPromises]).then(() => {
      if (ignore) return
      hydrateReset(mapped)
      setHydration({ record, failed: failedNested })
    })

    // Mark this run as stale when a newer run starts or component unmounts.
    return () => {
      ignore = true
    }
    // The dependency array is intentionally limited: every re-run calls reset()
    // and wipes unsaved edits, so `allShapes` and `resolveEntityLabel` are left
    // out on purpose and must not be added.
  }, [record, shape, reset, formSchema, resetNonce])

  // Publish form edits upward as the draft for this shape so switching shapes and
  // returning restores unsaved input. Guarded by hydratingRef so programmatic
  // resets (hydration) don't echo back, and gated on formSchema so a pre-hydration
  // empty form never overwrites a valid draft.
  useEffect(() => {
    if (!formSchema || !draftKey) return
    const subscription = watch((value) => {
      if (hydratingRef.current) return
      // Only new (create) forms publish a draft; edits are never preserved.
      if (record?.id) return
      onDraftChange?.(draftKey, value)
    })
    return () => subscription.unsubscribe()
  }, [watch, formSchema, draftKey, onDraftChange, record])

  // Track dirty predicates to build a minimal originalTriples delete set on update.
  const { dirtyFields } = formState

  function isFieldDirty(field, submitted) {
    if (field.type === 'nested') {
      // Bridge fields are driven by AnonymousEntityEditor through useFieldArray,
      // whose dirtyFields entry does not reliably report an appended/removed
      // card. Compare the bridge-node IRIs the record was loaded with against
      // the ones still in the form instead: any difference means the link
      // predicate must be rewritten, which is what puts it in originalTriples.
      // Without this, removing a connection sent no delete for the link, the
      // store kept it, and the "removed" connection came back on reload.
      // Sound only on a fully loaded form, which is why submit waits for
      // `hydration` and refuses one where a bridge node failed to load.
      if (dirtyFields[field.path]) return true
      const loaded = (record?.triples ?? [])
        .filter((t) => t.predicate === field.pathUri && t.objectType === 'iri')
        .map((t) => t.object)
      const current = new Set(
        (submitted?.[field.path] ?? []).map((entry) => entry?.['@id']).filter(Boolean)
      )
      return loaded.length !== current.size || loaded.some((iri) => !current.has(iri))
    }

    if (field.type === 'lang-string-list') {
      const df = dirtyFields[field.path]
      if (!df) return false
      // react-hook-form sets df to `true` when the array length changed (item added/removed)
      if (df === true) return true
      // When items were edited in place, df is an array of per-item dirty state objects
      if (Array.isArray(df)) return df.some((item) => item?.__value || item?.__lang)
      return false
    }

    if (field.type === 'lang-string') {
      // react-hook-form stores nested dirty state as an object on dirtyFields[field.path],
      // NOT as flat dot-notation keys. Access .__value and .__lang as object properties.
      return !!(dirtyFields[field.path]?.__value || dirtyFields[field.path]?.__lang)
    }

    return !!dirtyFields[field.path]
  }

  // A create form has nothing to load. An edit form is saveable only once the
  // current record (not a previous one) has loaded with every bridge node.
  const editLoading = !!record?.id && hydration.record !== record
  const editBlocked = !!record?.id && hydration.record === record && hydration.failed.length > 0

  async function onSubmit(formData) {
    if (editLoading || editBlocked) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const formDataWithId = { ...formData }
      if (record && record.id) {
        if (!formDataWithId['@id']) formDataWithId['@id'] = record.id
      }

      const newData = buildJsonLdEntity(formSchema.shape, formSchema.fields, formDataWithId)

      // Build originalTriples: only for predicates the user actually changed.
      // On create, record is null so changedOriginalTriples stays null and no delete runs.
      // Not gated on dirtyFields being non-empty: a bridge card removed through
      // useFieldArray may leave it empty, and an empty result is equivalent to
      // null for the backend (neither triggers a delete).
      let changedOriginalTriples = null
      if (record && record.triples) {
        const dirtyPathUris = new Set(
          formSchema.fields.filter((f) => isFieldDirty(f, formData)).map((f) => f.pathUri)
        )
        // Type selector changed: drop all existing rdf:type triples so the
        // stale concrete type (and re-asserted targetClass) don't linger
        // alongside the newly chosen type emitted by buildJsonLdEntity.
        if (formSchema.shape.typeOptions?.length > 0 && dirtyFields.__typeChoice) {
          dirtyPathUris.add(RDF_TYPE_URI)
        }
        changedOriginalTriples = record.triples.filter((t) => dirtyPathUris.has(t.predicate))
      }

      const payload = {
        shapeId: shape.id,
        data: newData,
        originalTriples: changedOriginalTriples,
      }

      const result = await apiClient.createEntity(payload)
      onValidation?.(result.validationReport)
      if (result.success) {
        // Report create vs update before the parent clears the record, then
        // blank the form in place (resetNonce re-seeds defaults) so the curator
        // can keep entering records without a jump to the list.
        onSaved?.(record?.id ? 'Record updated' : 'Record created')
        setResetNonce((n) => n + 1)
      }
    } catch (err) {
      setSubmitError(formatApiError(err))
    } finally {
      setSubmitting(false)
    }
  }

  function onInvalidSubmit(errors) {
    const rawPaths = collectErrorPaths(errors)
    const normalizedPaths = Array.from(
      new Set(rawPaths.map((p) => p.replace(/\.\d+\./g, '.').replace(/\.(__value|__lang)$/, '')))
    )

    const labelByPath = new Map((formSchema?.fields ?? []).map((f) => [f.path, f.name]))
    labelByPath.set('__typeChoice', 'Type')
    const labels = normalizedPaths.map((p) => labelByPath.get(p) ?? p)

    const message =
      labels.length > 0
        ? `Please fill required fields: ${labels.join(', ')}`
        : 'Form is invalid. Please check required fields.'

    setSubmitError(message)
  }

  if (!formSchema) {
    return <div className="form-loading">Loading form schema…</div>
  }

  // Block direct editing of helper-bridge shapes — they are only editable
  // inline via their parent entity form (AnonymousEntityEditor).
  if (shape.shapeRole === 'helper-bridge') {
    return (
      <div className="shape-form shape-form-blocked">
        <header className="form-header">
          <h2 className="form-title">{shape.label}</h2>
          {shape.description && <p className="form-description">{shape.description}</p>}
        </header>
        <div className="form-blocked-message">
          <p>
            This entity type is only editable as a connection from its parent entity. Please use the
            parent form to add or edit connections.
          </p>
        </div>
      </div>
    )
  }

  // Block editing of read-only shapes (reference vocabulary managed externally).
  if (shape.readOnly) {
    return (
      <div className="shape-form shape-form-blocked">
        <header className="form-header">
          <h2 className="form-title">{shape.label}</h2>
          {shape.description && <p className="form-description">{shape.description}</p>}
        </header>
        <div className="form-blocked-message">
          <p>
            This vocabulary is managed externally and cannot be edited here. Use the Records tab to
            browse available entries and select them from relation fields.
          </p>
        </div>
      </div>
    )
  }

  return (
    <form className="shape-form" onSubmit={handleSubmit(onSubmit, onInvalidSubmit)} noValidate>
      <header className="form-header">
        <h2 className="form-title">{shape.label}</h2>
        {shape.description && <p className="form-description">{shape.description}</p>}
        {record && record.id && (
          <span
            className="form-editing-label"
            style={{ display: 'block', fontSize: '0.9em', color: '#888', marginTop: '0.5em' }}
          >
            Editing <span style={{ fontFamily: 'monospace' }}>{record.id}</span>
          </span>
        )}
      </header>

      <div className="form-fields">
        {formSchema.shape.typeOptions?.length > 0 && (
          <div className="field-group">
            <label className="field-label" htmlFor="__typeChoice">
              Type<span className="field-required">*</span>
            </label>
            <Controller
              name="__typeChoice"
              control={control}
              defaultValue={''}
              rules={{ required: true }}
              render={({ field }) => (
                <StyledSelect
                  inputId="__typeChoice"
                  options={formSchema.shape.typeOptions}
                  value={field.value}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  selectRef={field.ref}
                  placeholder="— select —"
                  isClearable={false}
                />
              )}
            />
          </div>
        )}
        {formSchema.fields.map((field) => (
          <FormField
            key={field.path}
            field={field}
            allShapes={allShapes}
            register={register}
            control={control}
          />
        ))}
      </div>

      {submitError && <p className="form-error">{submitError}</p>}
      {editBlocked && (
        <p className="form-error">
          Could not load some connections ({hydration.failed.join(', ')}). Saving now would
          delete them, so it is disabled: reload the record to try again.
        </p>
      )}

      <footer className="form-actions">
        <button
          type="submit"
          className="btn btn-primary"
          disabled={submitting || editLoading || editBlocked}
        >
          {submitting
            ? record && record.id
              ? 'Updating…'
              : 'Inserting…'
            : editLoading
              ? 'Loading record…'
              : record && record.id
                ? 'Update record'
                : 'Insert record'}
        </button>
        {/* Reset clears the draft + selection in the parent and re-seeds a blank form. */}
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => {
            onReset?.()
            setResetNonce((n) => n + 1)
          }}
        >
          Reset
        </button>
      </footer>
    </form>
  )
}