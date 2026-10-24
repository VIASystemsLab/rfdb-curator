/**
 * Details panel for the selected node: its literal fields, its relations (both
 * directions, click to jump), and any external-authority links. A literal with
 * a `url` (an identifier stored as text, e.g. a VIAF ID) links to that page. Relation rows
 * resolve the neighbour's label from the shared node map so they read naturally.
 */
import { entityPageUrl } from '../api/client.js'
import { compactIri } from '../utils/prefixes.js'
import { kindColor, predicateLabel } from '../utils/types.js'

// Minimal file/document glyph (lucide's "file-text" outline) — inline rather
// than a dependency, since this is the only icon this app needs.
function DocumentIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  )
}

export default function Inspector({ node, edges, nodesById, onSelectNeighbor, onHide, onClose }) {
  if (!node) return null
  const rels = edges.filter((e) => e.source === node.id || e.target === node.id)
  const literals = node.details?.literals ?? []
  const externals = node.details?.externalLinks ?? []

  return (
    <aside className="inspector">
      <div className="inspector-header">
        <button className="inspector-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <span className="inspector-type" style={{ background: kindColor(node.kind) }}>
          {node.typeLabel}
        </span>
        <h3 className="inspector-title">{node.label}</h3>
        <div className="inspector-iri">
          {compactIri(node.id)}
          <a
            className="inspector-page-link"
            href={entityPageUrl(node.id)}
            target="_blank"
            rel="noopener noreferrer"
            title="Open entity page"
            aria-label="Open entity page"
          >
            <DocumentIcon />
          </a>
        </div>
      </div>

      <div className="inspector-body">
        {node.loading && <p className="picker-loading">Loading…</p>}

        {literals.length > 0 && (
          <>
            <div className="inspector-section-title">Fields</div>
            {literals.map((lit, i) => (
              <div className="kv" key={`${lit.predicate}-${i}`}>
                <div className="k">
                  {compactIri(lit.predicate)}
                  {lit.language ? ` @${lit.language}` : ''}
                </div>
                <div className="v">
                  {lit.url ? (
                    <a href={lit.url} target="_blank" rel="noopener noreferrer">
                      {lit.value}
                    </a>
                  ) : (
                    lit.value
                  )}
                </div>
              </div>
            ))}
          </>
        )}

        {rels.length > 0 && (
          <>
            <div className="inspector-section-title">Relations</div>
            {rels.map((e) => {
              const outgoing = e.source === node.id
              const otherId = outgoing ? e.target : e.source
              const other = nodesById[otherId]
              return (
                <div className="rel-row" key={e.id} onClick={() => onSelectNeighbor(otherId)}>
                  <div className="rel-pred">
                    <span className="rel-dir">{outgoing ? '→' : '←'}</span>
                    {predicateLabel(e.predicate)}
                  </div>
                  <div className="rel-target">{other?.label || compactIri(otherId)}</div>
                </div>
              )
            })}
          </>
        )}

        {node.truncated && (
          <p className="picker-empty">
            Showing the first {rels.length} relations; this node has more.
          </p>
        )}

        {externals.length > 0 && (
          <>
            <div className="inspector-section-title">External links</div>
            {externals.map((x, i) => (
              <div className="kv" key={`${x.predicate}-${i}`}>
                <div className="k">{compactIri(x.predicate)}</div>
                <div className="v">
                  <a href={x.target} target="_blank" rel="noreferrer">
                    {compactIri(x.target)}
                  </a>
                </div>
              </div>
            ))}
          </>
        )}

        {!node.expanded && !node.loading && (
          <p className="picker-empty">
            Use the node’s “Expand” or link-count button to load its relations.
          </p>
        )}
      </div>

      {onHide && (
        <div className="inspector-footer">
          <button className="btn inspector-hide" onClick={() => onHide(node.id)}>
            Hide from map
          </button>
        </div>
      )}
    </aside>
  )
}
