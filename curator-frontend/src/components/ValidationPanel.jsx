/**
 * Right-sidebar inspector and SHACL validation report panel.
 *
 * Three sections:
 *   Inspector  - shows the compact IRI and label of the selected record,
 *                then fetches its triples from GET /api/v1/dataexplorer/entities/get?id=.
 *   Validation - renders the SHACL report from the last form save.
 *                Each violation shows the property path + message.
 *   Triples    - lists every {predicate, object} triple for the entity.
 *                Both values are compacted to CURIEs via compactIri().
 *
 * Props:
 *   validation  {object|null}   - ValidationResult from the last POST /api/v1/curator/entities
 *   record      {object|null}   - The currently selected record {id, label}
 *   onNavigate  {function}      - Called with an IRI when a linked internal
 *                                 record is clicked, to bring up its view.
 *   dataBaseUri {string|null}   - Namespace this deployment's records live under
 *                                 (DATA_BASE_URI, from GET /api/v1/dataexplorer/meta/schema).
 *                                 Objects under it are navigable; everything else
 *                                 (classes, external IRIs, vocab) is plain text.
 *                                 While it is unknown, nothing is a link.
 */
import { useEffect, useState } from 'react'
import { apiClient } from '../api/client.js'
import Icon from './Icon.jsx'
import { compactIri } from '../utils/prefixes.js'
import './ValidationPanel.css'

const RDFS_LABEL = 'http://www.w3.org/2000/01/rdf-schema#label'

function renderObjectLabel(triple) {
  if (triple.objectType === 'literal') {
    return `"${triple.object}"`
  }
  return compactIri(triple.object)
}

function renderObjectAnnotation(triple) {
  if (triple.objectType !== 'literal') return null
  if (triple.language) return `@${triple.language}`
  if (triple.datatype) return `^^${compactIri(triple.datatype)}`
  return null
}

export default function ValidationPanel({ validation, record, onNavigate, dataBaseUri }) {
  const [entity, setEntity] = useState(null)

  useEffect(() => {
    if (!record?.id) {
      setEntity(null)
      return
    }
    apiClient
      .getEntity(record.id)
      .then(setEntity)
      .catch(() => setEntity(null))
  }, [record])

  // Render a summary using the same visual style as the triples view
  let summary = null
  if (entity?.triples?.length) {
    // Group triples by predicate
    const grouped = {}
    for (const triple of entity.triples) {
      const key = compactIri(triple.predicate)
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(triple)
    }
    summary = (
      <ul className="triple-list">
        {Object.entries(grouped).map(([pred, triples]) => (
          <li key={pred} className="triple-item">
            <span className="mono triple-predicate">{pred}</span>
            <span className="triple-object">
              {triples.map((triple, i) => {
                const annotation = renderObjectAnnotation(triple)
                const isRecordLink =
                  Boolean(dataBaseUri) &&
                  triple.objectType !== 'literal' &&
                  triple.object.startsWith(dataBaseUri)
                return (
                  <span key={i}>
                    {isRecordLink ? (
                      <button
                        type="button"
                        className="triple-link"
                        onClick={() => onNavigate?.(triple.object)}
                        title={`View ${compactIri(triple.object)}`}
                      >
                        {compactIri(triple.object)}
                      </button>
                    ) : (
                      renderObjectLabel(triple)
                    )}
                    {annotation ? (
                      <span className="triple-object-annotation"> {annotation}</span>
                    ) : null}
                    {i < triples.length - 1 ? ', ' : ''}
                  </span>
                )
              })}
            </span>
          </li>
        ))}
      </ul>
    )
  }

  return (
    <div className="validation-panel">
      <section className="inspector-block">
        <h3 className="inspector-title">Inspector</h3>
        {record ? (
          <>
            <p className="inspector-id mono">
              {compactIri(record.id)}
              <a
                className="inspector-page-link"
                href={apiClient.entityPageUrl(record.id)}
                target="_blank"
                rel="noopener noreferrer"
                title="Open entity page"
                aria-label="Open entity page"
              >
                <Icon name="FileText" size={13} />
              </a>
            </p>
            <p className="inspector-label">
              {record.label ??
                entity?.triples?.find((t) => t.predicate === RDFS_LABEL)?.object ??
                'Untitled record'}
            </p>
            {apiClient.explorerRecordUrl(record.id) && (
              <a
                className="triple-link inspector-explorer-link"
                href={apiClient.explorerRecordUrl(record.id)}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open in Explorer ↗
              </a>
            )}
            {summary}
          </>
        ) : (
          <p className="inspector-empty">Select a record to inspect it.</p>
        )}
      </section>

      <section className="inspector-block">
        <h3 className="inspector-title">Validation</h3>
        {validation ? (
          validation.conforms ? (
            <p className="validation-ok">Conforms to SHACL.</p>
          ) : (
            <ul className="validation-list">
              {validation.violations.map((item, index) => (
                <li key={`${item.path}-${index}`} className="validation-item">
                  <span className="validation-path mono">
                    {compactIri(item.path) ?? 'unknown path'}
                  </span>
                  <span>{item.message}</span>
                </li>
              ))}
            </ul>
          )
        ) : (
          <p className="inspector-empty">No validation report yet.</p>
        )}
      </section>
    </div>
  )
}
