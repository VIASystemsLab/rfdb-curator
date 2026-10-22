/**
 * Root application component.
 *
 * Layout (three columns):
 *   [< nav sidebar ]  [< tabbed form/records panel ]  [< inspector sidebar ]
 *
 * State:
 *   - `activeShape`       -- the currently selected SHACL NodeShape (drives both form and list)
 *   - `activeView`        -- `'form'` | `'records'` -- which tab is visible in the middle panel
 *   - `selectedRecord`    -- the summary record (id, label) picked in the list; shown in the inspector
 *   - `loadedRecord`      -- the full entity fetched for `selectedRecord`; ShapeForm's record prop
 *   - `recordLoading`     -- true while that fetch is in flight
 *   - `validation`        -- the SHACL report returned after a form save
 *   - `shapeCounts`       -- {shapeId: count} map for the sidebar count pills
 *   - `recordsRefreshKey` -- increment to force ShapeRecordList + counts to re-fetch
 *
 * Edit flow:
 *   1. User clicks pencil (edit) button in records list.
 *   2. setSelectedRecord(record) and setActiveView('form') are called.
 *   3. useEffect triggers on selectedRecord, sets recordLoading=true, fetches entity data.
 *   4. While loading, show 'Loading record…' in the form panel.
 *   5. When loaded, setLoadedRecord(data), set recordLoading=false, render ShapeForm with loadedRecord.
 *
 * --- IMPORTANT: CREATE vs UPDATE ---
 * - When editing, App fetches the full entity and passes it as the record prop to ShapeForm.
 * - ShapeForm must ensure @id is included in the form state and payload for updates.
 * - If the fetch fails, loadedRecord stays null and ShapeForm renders as a blank create
 *   form rather than an error (a known gap, tracked separately).
 *
 * View details flow:
 *   1. User clicks eye (see details) button in records list.
 *   2. setSelectedRecord(record) and setActiveView('records') are called.
 *   3. ValidationPanel shows details for selectedRecord in inspector sidebar.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import { apiClient } from './api/client.js'
import Icon from './components/Icon.jsx'
import ShapeForm from './components/ShapeForm.jsx'
import DataContextPanel from './components/DataContextPanel.jsx'
import ShapeRecordList from './components/ShapeRecordList.jsx'
import ValidationPanel from './components/ValidationPanel.jsx'
import WelcomeGuide from './components/WelcomeGuide.jsx'
import { hydratePrefixes } from './utils/prefixes.js'

// localStorage flag: once a curator dismisses the welcome guide it stays closed on reload.
const GUIDE_SEEN_KEY = 'rfdb.guideSeen'

// sessionStorage key + TTL for create-form drafts (see the `drafts` state below). Drafts let
// an in-progress *new* form survive both in-app navigation and a browser reload; the TTL
// drops a stale cache on reload so an abandoned draft can't resurface an hour later.
const DRAFTS_KEY = 'rfdb.formDrafts'
const DRAFTS_TTL_MS = 60 * 60 * 1000 // 1 hour

/**
 * Read persisted create-form drafts from sessionStorage, discarding the whole cache if it
 * is older than DRAFTS_TTL_MS. sessionStorage can throw (private mode / disabled storage)
 * and must never block the editor, so every access is guarded and degrades to `{}`.
 */
function loadPersistedDrafts() {
  try {
    const raw = sessionStorage.getItem(DRAFTS_KEY)
    if (!raw) return {}
    const { savedAt, drafts } = JSON.parse(raw)
    if (!savedAt || Date.now() - savedAt > DRAFTS_TTL_MS) {
      sessionStorage.removeItem(DRAFTS_KEY)
      return {}
    }
    return drafts && typeof drafts === 'object' ? drafts : {}
  } catch {
    return {}
  }
}

export default function App() {
  // --- Application state ---
  const [shapes, setShapes] = useState([]) // all SHACL shapes from /api/v1/dataexplorer/shapes
  const [shapesError, setShapesError] = useState(null) // visible sidebar error when shape fetch fails
  const [shapeCounts, setShapeCounts] = useState({}) // {shapeId: int} for sidebar pills
  const [activeShape, setActiveShape] = useState(null) // currently selected shape
  const [selectedRecord, setSelectedRecord] = useState(null) // summary record selected from list (id, label, etc.)
  const [loadedRecord, setLoadedRecord] = useState(null) // full entity data for editing (fetched from backend)
  const [validation, setValidation] = useState(null) // last SHACL report
  const [loadingShapes, setLoadingShapes] = useState(true)
  const [recordsRefreshKey, setRecordsRefreshKey] = useState(0) // bump to re-fetch list
  const [activeView, setActiveView] = useState('form') // 'form' | 'records'
  const [recordLoading, setRecordLoading] = useState(false) // true while fetching entity data for editing
  const [guideOpen, setGuideOpen] = useState(false) // first-time curator welcome guide (WEMI overlay)
  const [showContext, setShowContext] = useState(false) // read-only Data Context Panel in the main area
  const [toast, setToast] = useState('') // transient bottom-right confirmation (e.g. after save)
  const [dataBaseUri, setDataBaseUri] = useState(null) // DATA_BASE_URI, from /api/v1/dataexplorer/meta/schema
  // Draft cache keyed by `shape::<shapeId>`. Single slot per shape form (last state wins),
  // so unsaved input survives shape/record navigation even though ShapeForm re-hydrates
  // via reset() on every shape/record change. Seeded from sessionStorage (with a 1h TTL)
  // so a create form also survives a browser reload; kept in sync by the effect below.
  const [drafts, setDrafts] = useState(loadPersistedDrafts)

  // Draft key for the active shape form. Keyed by shape only (not record) so a draft
  // survives switching away and back; ShapeForm rebinds @id from the live record.
  const draftKey = activeShape ? `shape::${activeShape.id}` : null

  // Stable across renders (only closes over setDrafts) so ShapeForm's watch
  // subscription is not torn down and rebuilt on every keystroke-driven re-render.
  // Only new (create) forms keep a draft; editing an existing record always reloads
  // from that record, so drafts never carry an @id and can never turn into an update.
  const handleDraftChange = useCallback((key, value) => {
    setDrafts((prev) => ({ ...prev, [key]: value }))
  }, [])

  const handleDraftClear = useCallback((key) => {
    setDrafts((prev) => {
      if (!key || !(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }, [])

  // Mirror the draft cache into sessionStorage so an in-progress create form survives a
  // reload. Stamped with `savedAt` for the TTL checked in loadPersistedDrafts(); cleared
  // outright when the cache empties — which is what onSaved/onReset do via handleDraftClear,
  // so submitting or clicking Reset also wipes the persisted copy. Guarded because storage
  // can throw; a failure just means drafts won't outlive the reload.
  //
  // The mount run is skipped: loadPersistedDrafts() already seeded state from storage, and
  // re-stamping here would reset the TTL on every reload. Only genuine edits/clears re-stamp,
  // so the 1h expiry is measured from the last edit and a bare reload never extends it.
  const draftsMounted = useRef(false)
  useEffect(() => {
    if (!draftsMounted.current) {
      draftsMounted.current = true
      return
    }
    try {
      if (Object.keys(drafts).length === 0) {
        sessionStorage.removeItem(DRAFTS_KEY)
      } else {
        sessionStorage.setItem(DRAFTS_KEY, JSON.stringify({ savedAt: Date.now(), drafts }))
      }
    } catch {
      // sessionStorage unavailable (private mode / disabled) — non-fatal.
    }
  }, [drafts])

  /** Pull per-shape record counts from the backend and sync `shapeCounts`. */
  function refreshShapeCounts() {
    apiClient
      .getDataCounts()
      .then((data) => setShapeCounts(data?.counts ?? {}))
      .catch(() => setShapeCounts({}))
  }

  /** Format a raw count number with locale-aware thousands separators. */
  function formatCount(value) {
    const n = Number(value ?? 0)
    return Number.isFinite(n) ? Intl.NumberFormat('en-US').format(n) : '0'
  }

  function getShapeLoadErrorMessage(error) {
    const status = error?.response?.status
    const detail = error?.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) {
      return `Failed to load shapes (${status ?? 'error'}): ${detail}`
    }
    if (error?.message) {
      return `Failed to load shapes: ${error.message}`
    }
    return 'Failed to load shapes: unknown error.'
  }

  function loadShapes() {
    setLoadingShapes(true)
    setShapesError(null)
    apiClient
      .getShapes()
      .then((data) => {
        const sorted = [...data].sort((a, b) => (a.label || a.id).localeCompare(b.label || b.id))
        setShapes(sorted)
        if (sorted.length > 0) {
          setActiveShape(sorted[0])
          return
        }
        setActiveShape(null)
        setShapesError(
          'No shapes returned by /api/v1/dataexplorer/shapes. Check backend startup logs and schema/rfdb_schema.ttl syntax.'
        )
      })
      .catch((error) => {
        setShapes([])
        setActiveShape(null)
        setShapesError(getShapeLoadErrorMessage(error))
      })
      .finally(() => setLoadingShapes(false))
  }

  useEffect(() => {
    loadShapes()

    refreshShapeCounts()

    // Hydrate prefix map in parallel with shape loading.
    // On failure degrade gracefully: IRI compaction and JSON-LD @context will be
    // empty until the next reload, but the app remains fully functional.
    apiClient.getPrefixes().then(hydratePrefixes).catch(() => {
      console.warn(
        'Failed to fetch prefix map from /api/v1/dataexplorer/meta/prefixes; IRI compaction disabled.',
      )
    })

    // The namespace that marks an IRI as one of our records. On failure the
    // inspector shows record IRIs as plain text instead of links.
    apiClient
      .getSchemaInfo()
      .then((info) => setDataBaseUri(info.dataBaseUri || null))
      .catch(() => {
        console.warn(
          'Failed to fetch schema info from /api/v1/dataexplorer/meta/schema; record links disabled.',
        )
      })
  }, [])

  useEffect(() => {
    refreshShapeCounts()
  }, [recordsRefreshKey])

  // Auto-open the welcome guide on a curator's first visit. Wrapped in try/catch:
  // localStorage can throw (private mode / disabled storage) and must never block the editor.
  useEffect(() => {
    try {
      if (!localStorage.getItem(GUIDE_SEEN_KEY)) setGuideOpen(true)
    } catch {
      setGuideOpen(true) // storage unavailable: show it anyway (may re-show each load)
    }
  }, [])

  function closeGuide() {
    setGuideOpen(false)
    try {
      localStorage.setItem(GUIDE_SEEN_KEY, '1')
    } catch {
      /* storage unavailable — guide simply re-opens next load */
    }
  }

  // Auto-dismiss the toast a few seconds after it appears.
  useEffect(() => {
    if (!toast) return undefined
    const timer = setTimeout(() => setToast(''), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  // --- Handler: view a record in the inspector (from the list or an inspector link) ---
  function handleViewRecord(record) {
    setSelectedRecord(record)
    // The viewed record also becomes the form's record, so returning to the Form
    // tab opens it for editing in place of whatever was there before.
    setActiveView('records')
    setShowContext(false)
  }

  // --- Handler: user selects a shape in the sidebar ---
  function handleShapeSelect(shape) {
    setActiveShape(shape)
    setSelectedRecord(null)
    setLoadedRecord(null)
    setValidation(null)
    // Helper-bridge shapes (AgentRole, DigitalCopy) are never edited directly —
    // they are created/edited from their parent's form. Land on the browse-only
    // Records view; the Form tab is hidden for them below.
    setActiveView(shape.shapeRole === 'helper-bridge' ? 'records' : 'form')
    setShowContext(false) // picking a shape leaves the Data Context Panel
  }

  // --- Effect: when a record is selected, for editing or for viewing, fetch its full data ---
  // The result feeds ShapeForm's record prop in both cases.
  useEffect(() => {
    if (!selectedRecord) {
      setLoadedRecord(null)
      setRecordLoading(false)
      return
    }
    setRecordLoading(true)
    apiClient
      .getEntity(selectedRecord.id)
      .then((res) => {
        setLoadedRecord(res)
        setRecordLoading(false)
      })
      .catch(() => {
        setLoadedRecord(null)
        setRecordLoading(false)
      })
  }, [selectedRecord])

  // Helper-bridge shapes (no rdfs:label — e.g. AgentRole, DigitalCopy) are set
  // aside at the bottom of the nav and are browse-only; everything else is a
  // directly-editable entity.
  const helperShapes = shapes.filter((s) => s.shapeRole === 'helper-bridge')
  const mainShapes = shapes.filter((s) => s.shapeRole !== 'helper-bridge')
  const isHelperShape = activeShape?.shapeRole === 'helper-bridge'
  // Helpers force the Records (browse) view regardless of any stale activeView.
  const effectiveView = isHelperShape ? 'records' : activeView

  const renderNavItem = (shape) => (
    <li key={shape.id}>
      <button
        className={`nav-item ${activeShape?.id === shape.id ? 'active' : ''} ${
          shape.readOnly ? 'nav-item--readonly' : ''
        } ${shape.shapeRole === 'helper-bridge' ? 'nav-item--helper' : ''}`}
        onClick={() => handleShapeSelect(shape)}
      >
        <span className="nav-item-label">{shape.label}</span>
        <span className="nav-item-right">
          {shape.readOnly && (
            <Icon name="Lock" size={11} className="nav-lock-icon" aria-label="Read-only" />
          )}
          <span className="nav-count-pill">{formatCount(shapeCounts[shape.id])}</span>
        </span>
      </button>
    </li>
  )

  return (
    <div className="app-layout">
      {/* ── Left navigation ── */}
      <nav className="app-nav">
        <div>
          <p className="app-title">RossijskijFeatrDB</p>
          <p className="app-subtitle">Data Editor</p>
          <div className="nav-actions">
            <button
              className="nav-help"
              onClick={() => setGuideOpen(true)}
              title="How records fit together (WEMI guide)"
            >
              <Icon name="CircleHelp" size={14} />
              Getting started
            </button>
            <button
              className={`nav-help ${showContext ? 'nav-help--active' : ''}`}
              onClick={() => setShowContext(true)}
              title="Runtime graph configuration (read-only)"
            >
              <Icon name="Database" size={14} />
              Data context
            </button>
          </div>
        </div>
        {loadingShapes ? (
          <p className="nav-loading">Loading shapes…</p>
        ) : shapesError ? (
          <div className="nav-error" role="alert">
            <p className="nav-error-text">{shapesError}</p>
            <button className="nav-error-retry" onClick={loadShapes}>
              Retry
            </button>
          </div>
        ) : (
          <>
            <ul className="shape-nav">{mainShapes.map(renderNavItem)}</ul>
            {helperShapes.length > 0 && (
              <>
                <div className="nav-divider" role="separator" />
                <p className="nav-section-label">Managed within records</p>
                <ul className="shape-nav shape-nav--helpers">
                  {helperShapes.map(renderNavItem)}
                </ul>
              </>
            )}
          </>
        )}
      </nav>

      {/* ── Main area with tabbed content + inspector ── */}
      <main className="app-main">
        {showContext ? (
          <DataContextPanel />
        ) : activeShape ? (
          <>
            <section className="panel-content">
              <div className="view-tabs" role="tablist" aria-label="Shape view tabs">
                {/* Helper-bridge shapes are not editable here → no Form tab. */}
                {!isHelperShape && (
                  <button
                    className={`view-tab ${effectiveView === 'form' ? 'active' : ''}`}
                    role="tab"
                    aria-selected={effectiveView === 'form'}
                    onClick={() => setActiveView('form')}
                  >
                    Form
                  </button>
                )}
                <button
                  className={`view-tab ${effectiveView === 'records' ? 'active' : ''}`}
                  role="tab"
                  aria-selected={effectiveView === 'records'}
                  onClick={() => setActiveView('records')}
                >
                  Records
                </button>
              </div>

              {effectiveView === 'form' ? (
                <div className="panel-form">
                  {/*
                    If editing an existing record, show a loading message until the full entity data is fetched.
                    Only render the form when not loading, or when creating a new record.
                  */}
                  {selectedRecord && recordLoading && (
                    <div className="form-loading">Loading record…</div>
                  )}
                  {/*
                    This guard has a known gap, tracked separately: on the first render after onEdit,
                    recordLoading is still false, so ShapeForm briefly renders with the previous loadedRecord.
                  */}
                  {(!selectedRecord || !recordLoading) && (
                    <ShapeForm
                      shape={activeShape}
                      allShapes={shapes}
                      record={loadedRecord}
                      draftKey={draftKey}
                      draftValue={draftKey ? drafts[draftKey] : undefined}
                      onDraftChange={handleDraftChange}
                      onValidation={setValidation}
                      onSaved={(message) => {
                        // Stay on the form: ShapeForm blanks itself, and a toast
                        // confirms — no jarring jump to the record list.
                        handleDraftClear(draftKey)
                        setSelectedRecord(null)
                        setLoadedRecord(null)
                        setRecordsRefreshKey((k) => k + 1) // refresh list + sidebar counts
                        setToast(message || 'Record saved')
                      }}
                      onReset={() => {
                        handleDraftClear(draftKey)
                        setSelectedRecord(null)
                        setLoadedRecord(null)
                      }}
                    />
                  )}
                </div>
              ) : (
                <div className="panel-list">
                  <ShapeRecordList
                    shape={activeShape}
                    selected={selectedRecord}
                    refreshKey={recordsRefreshKey}
                    // --- Handler: user clicks pencil (edit) button ---
                    onEdit={(record) => {
                      setSelectedRecord(record)
                      setActiveView('form') // Switch to form for editing
                    }}
                    // --- Handler: user clicks eye (see details) button ---
                    onView={handleViewRecord}
                    onDelete={() => {
                      setRecordsRefreshKey((k) => k + 1)
                    }}
                  />
                </div>
              )}
            </section>

            <aside className="panel-inspector">
              <ValidationPanel
                validation={validation}
                record={selectedRecord}
                onNavigate={(iri) => handleViewRecord({ id: iri })}
                dataBaseUri={dataBaseUri}
              />
            </aside>
          </>
        ) : (
          <div className="app-empty">
            <p className="app-empty-title">Select a shape to begin</p>
          </div>
        )}
      </main>

      <WelcomeGuide open={guideOpen} onClose={closeGuide} />

      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </div>
  )
}
