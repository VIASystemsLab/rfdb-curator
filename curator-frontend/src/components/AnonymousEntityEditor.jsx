/**
 * Inline editor for nested (bridge/helper) graph structures.
 *
 * Rendered as a nested editor inside a parent entity form (e.g., Source, Work)
 * for bridge/helper shapes (like AgentRole).
 *
 * Policy:
 *   - Bridge/helper entities (e.g., AgentRole) are only created/edited inline as connections from their parent entity.
 *   - ShapeForm blocks their own top-level form, with a message to use the parent entity form.
 *
 * Shape role drives rendering:
 *   helper-bridge   - the nested shape is a pure relation container (no rdfs:label of its own).
 *                    Each inline card shows EntitySearch widgets for every entity-search property (core:isMemberOf, core:hasRole).
 *   standalone-entity - the nested shape has its own label; a note is shown directing the user to create it separately then reference it via search.
 *
 * Node creation:
 *   - A new bridge/helper entry has no @id: it is sent as a blank node and the backend names it
 *     (<DATA_BASE_URI><ShapeName>_<12hex>). Entries loaded from the store keep their IRI.
 *
 * Props:
 *   field      {object}  - Property descriptor (path, nestedShape, parentShape if nested)
 *   allShapes  {array}   - All shapes (used to resolve the nested shape metadata)
 *   control    {object}  - react-hook-form control (drives useFieldArray)
 */
import { useFieldArray } from 'react-hook-form'
import EntitySearch from './EntitySearch.jsx'
import './AnonymousEntityEditor.css'

function getNestedShape(field, allShapes) {
  if (!field?.nestedShape || !Array.isArray(allShapes)) return null
  return allShapes.find((shape) => shape.id === field.nestedShape) ?? null
}

export default function AnonymousEntityEditor({ field, allShapes, control }) {
  const nestedShape = getNestedShape(field, allShapes)
  const { fields, append, remove } = useFieldArray({
    control,
    name: field.path,
  })

  // Helper-bridge shapes (e.g., AgentRole) are only editable inline via parent entity.
  // The disabling logic for top-level forms is handled in ShapeForm.jsx.
  const isHelperBridge = nestedShape?.shapeRole === 'helper-bridge'

  function addHelperEntry() {
    // No @id: the backend names a new entry when the record is saved.
    append({ '@type': nestedShape?.targetClass ?? '' })
  }

  return (
    <div className="nested-editor">
      <div className="nested-editor-header">
        <span className="nested-editor-title">
          {isHelperBridge ? 'Connections' : 'Inline entries'}
        </span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={addHelperEntry}>
          Add
        </button>
      </div>

      {nestedShape && !isHelperBridge && (
        <p className="nested-editor-note">
          Nested shape <span className="mono">{nestedShape.label}</span> is modeled as a standalone
          entity. Use reference fields to associate existing records.
        </p>
      )}

      {fields.length === 0 && <p className="nested-editor-empty">No inline entities yet.</p>}

      <div className="nested-editor-list">
        {fields.map((item, index) => (
          <div key={item.id} className="nested-card">
            <div className="nested-card-header">
              <span className="nested-card-title mono">Entry {index + 1}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => remove(index)}>
                Remove
              </button>
            </div>
            {isHelperBridge && nestedShape && (
              <div className="nested-fields">
                {nestedShape.properties
                  .filter((prop) => prop.path !== 'rdf:type')
                  .map((prop) => {
                    const nestedName = `${field.path}.${index}.${prop.path}`
                    if (prop.type === 'entity-search') {
                      return (
                        <div key={nestedName} className="field-group">
                          <label className="field-label">{prop.name}</label>
                          <EntitySearch field={prop} control={control} name={nestedName} />
                        </div>
                      )
                    }
                    return (
                      <div key={nestedName} className="field-group">
                        <label className="field-label">{prop.name}</label>
                        <input
                          className="field-input"
                          value={item[prop.path] ?? ''}
                          readOnly
                          placeholder="Bridge helper uses existing entity associations"
                        />
                      </div>
                    )
                  })}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
