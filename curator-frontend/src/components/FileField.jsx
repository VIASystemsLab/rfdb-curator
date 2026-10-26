/**
 * File-upload form field (schema-driven, upload-first flow).
 *
 * Rendered by FormField for any property the backend types as `file-list` —
 * i.e. whose SHACL nested shape targets schema:DigitalDocument. A digital copy
 * behaves like a bridge node whose fields are machine-filled: "Upload PDF"
 * stages the bytes (POST /api/v1/curator/files/staged) and appends the returned prefilled
 * node to form state; on submit it travels inside the JSON-LD payload like any
 * nested node (see utils/jsonld.js). Works on unsaved forms too.
 *
 * Removing an entry only edits form state — the link triple disappears on the
 * next submit and the orphaned object is collected by the backend cleanup
 * script. Metadata shown here is a prefill; the server re-derives it at write
 * time.
 */
import { useState } from 'react'
import { Controller } from 'react-hook-form'
import { apiClient } from '../api/client.js'
import { formatBytes } from '../utils/format.js'
import Icon from './Icon.jsx'
import './FileField.css'

function entryId(entry) {
  return entry['@id'] ?? entry.id
}

function entryFileId(entry) {
  return entry.fileId ?? String(entryId(entry) ?? '').split('/').pop()
}

/**
 * Download link for one entry, correct for either lifecycle state.
 *
 * The two states live on different services, and each entry's `contentUrl`
 * already identifies its own state: a freshly staged node gets the writer's
 * relative preview path from `stageFile`, while an entry loaded from a saved
 * record carries the absolute published URI, which `resolveFileUrl` rebases
 * onto the reader. So resolving the node's own `contentUrl` is enough, with no
 * need to track which state this is.
 *
 * Falls back to the published route when a node carries no `contentUrl`.
 */
function entryHref(entry) {
  return apiClient.resolveFileUrl(entry.contentUrl) || apiClient.fileDownloadUrl(entryFileId(entry))
}

export default function FileField({ field, control }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  return (
    <Controller
      name={field.path}
      control={control}
      defaultValue={[]}
      render={({ field: rhf }) => {
        const entries = Array.isArray(rhf.value) ? rhf.value : []

        async function onUpload(event) {
          const file = event.target.files?.[0]
          event.target.value = '' // allow re-selecting the same file after an error
          if (!file) return
          setBusy(true)
          setError(null)
          try {
            const node = await apiClient.stageFile(file)
            rhf.onChange([...entries, node])
          } catch (err) {
            setError(err?.response?.data?.detail || err?.message || 'Upload failed')
          } finally {
            setBusy(false)
          }
        }

        function onRemove(id) {
          rhf.onChange(entries.filter((entry) => entryId(entry) !== id))
        }

        return (
          <div className="file-field">
            {entries.length === 0 ? (
              <p className="file-field-empty">No digital copies attached.</p>
            ) : (
              <ul className="file-field-list">
                {entries.map((entry) => (
                  <li key={entryId(entry)} className="file-field-item">
                    <Icon name="FileText" size={15} className="file-field-icon" />
                    <a
                      className="file-field-name"
                      href={entryHref(entry)}
                      target="_blank"
                      rel="noreferrer"
                      title={`Download ${entry.name || entryFileId(entry)}`}
                    >
                      {entry.name || entryFileId(entry)}
                    </a>
                    <span className="file-field-meta">
                      {formatBytes(entry.contentSize)}
                      {entry.numberOfPages ? ` · ${entry.numberOfPages} pp` : ''}
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost file-field-remove"
                      onClick={() => onRemove(entryId(entry))}
                      disabled={busy}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label className="btn btn-secondary file-field-upload">
              <Icon name="Upload" size={14} />
              {busy ? 'Uploading…' : 'Upload PDF'}
              <input type="file" accept="application/pdf" onChange={onUpload} disabled={busy} hidden />
            </label>
            {error && <p className="file-field-error">{error}</p>}
          </div>
        )
      }}
    />
  )
}
