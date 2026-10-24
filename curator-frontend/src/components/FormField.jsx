/**
 * Single form field renderer: maps a SHACL property descriptor to an input widget.
 *
 * Field type -> widget:
 *   enum             - <StyledSelect> (react-select) populated from sh:in values
 *   entity-search    - async autocomplete (<EntitySearch>) via /api/v1/dataexplorer/entities/search
 *   nested           - inline blank-node editor (<AnonymousEntityEditor>)
 *   file-list        - PDF upload list for digital copies (<FileField>)
 *   year             - number input clamped to [800, 2100]
 *   temporal         - text input accepting YYYY, YYYY-MM or YYYY-MM-DD
 *   number           - unbounded number input
 *   uri              - URL input for external IRIs (owl:sameAs, etc.),
 *                      or a <UriList> when the property allows several values
 *   lang-string      - text + language dropdown; stored as {__value, __lang}
 *   lang-string-list - one text + language row per value (<LangStringList>)
 *   default          - plain text input
 *
 * Fields flagged `longText` by the backend (their predicate is in the
 * LONG_TEXT_PREDICATES setting) render a multi-line <textarea> instead of a
 * single-line input — applies to the `default` and single `lang-string` widgets.
 *
 * A `default` field with a `linkTemplate` holds an external identifier as text
 * (a VIAF or RISM ID; the EXTERNAL_ID_LINKS setting). Once its value is valid,
 * a link to the authority's page shows under the input.
 *
 * Props:
 *   field      {object}    - Property descriptor from /api/v1/curator/forms
 *   allShapes  {array}     - All shapes, forwarded to AnonymousEntityEditor
 *   register   {function}  - react-hook-form register
 *   control    {object}    - react-hook-form control (needed by Controller-based widgets)
 */
import AnonymousEntityEditor from './AnonymousEntityEditor.jsx'
import EntitySearch from './EntitySearch.jsx'
import { Controller } from 'react-hook-form'

import FileField from './FileField.jsx'
import LangStringList from './LangStringList.jsx'
import StyledSelect from './StyledSelect.jsx'
import UriList from './UriList.jsx'
import { externalIdUrl } from '../utils/externalIds.js'
import { LANG_OPTIONS, languageLabel } from '../utils/languages.js'

export default function FormField({ field, allShapes, register, control }) {
  const {
    path,
    name,
    type,
    description,
    minCount,
    longText,
    linkTemplate,
    pattern,
    in: options = [],
  } = field
  const isRequired = minCount > 0
  const languageTagPolicy = field.languageTagPolicy ?? 'not-applicable'
  const hasNoLanguageOption = languageTagPolicy === 'optional'
  const requiresLanguageTag = languageTagPolicy === 'required'

  const label = (
    <label className="field-label" htmlFor={path}>
      {name}
      {isRequired && <span className="field-required">*</span>}
      {description && <span className="field-description">{description}</span>}
    </label>
  )

  // ── LangStringList ─────────────────────────────────────────────
  if (type === 'lang-string-list') {
    return (
      <div className="field-group">
        {label}
        <LangStringList path={path} label={null} control={control} isRequired={isRequired} />
      </div>
    )
  }

  // ── Enum / sh:in ───────────────────────────────────────────────
  if (type === 'enum') {
    const enumOptions = options.map((v) => ({
      value: v,
      label: v.includes(':') ? v.split(':').pop() : v,
    }))
    return (
      <div className="field-group">
        {label}
        <Controller
          name={path}
          control={control}
          defaultValue={''}
          rules={{ required: isRequired }}
          render={({ field }) => (
            <StyledSelect
              inputId={path}
              options={enumOptions}
              value={field.value}
              onChange={field.onChange}
              onBlur={field.onBlur}
              selectRef={field.ref}
              placeholder="— select —"
              isClearable={!isRequired}
            />
          )}
        />
      </div>
    )
  }

  // ── Entity search / autocomplete ──────────────────────────────────
  if (type === 'entity-search') {
    return (
      <div className="field-group">
        {label}
        <EntitySearch field={field} control={control} />
      </div>
    )
  }

  // ── File upload (digital copies — machine-filled bridge nodes) ─────
  if (type === 'file-list') {
    return (
      <div className="field-group">
        {label}
        <FileField field={field} control={control} />
      </div>
    )
  }

  // ── Nested blank node form ─────────────────────────────────────────
  if (type === 'nested') {
    return (
      <div className="field-group">
        {label}
        <AnonymousEntityEditor field={field} allShapes={allShapes} control={control} />
      </div>
    )
  }

  // ── Year ──────────────────────────────────────────────────────────
  if (type === 'year') {
    return (
      <div className="field-group">
        {label}
        <input
          id={path}
          className="field-input"
          type="number"
          min="800"
          max="2100"
          placeholder="e.g. 1736"
          {...register(path, { required: isRequired, min: 800, max: 2100 })}
        />
      </div>
    )
  }

  // ── Temporal (gYear / gYearMonth / date) ────────────────────────
  if (type === 'temporal') {
    return (
      <div className="field-group">
        {label}
        <input
          id={path}
          className="field-input"
          type="text"
          placeholder="YYYY or YYYY-MM or YYYY-MM-DD"
          {...register(path, { required: isRequired })}
        />
      </div>
    )
  }

  // ── Number ────────────────────────────────────────────────────────
  if (type === 'number') {
    return (
      <div className="field-group">
        {label}
        <input
          id={path}
          className="field-input"
          type="number"
          step="any"
          {...register(path, { required: isRequired })}
        />
      </div>
    )
  }

  // ── URI input ─────────────────────────────────────────────────────
  if (type === 'uri') {
    const isMulti = field.maxCount !== 1
    if (isMulti) {
      return (
        <div className="field-group">
          {label}
          <UriList path={path} label={null} control={control} isRequired={isRequired} />
        </div>
      )
    }

    return (
      <div className="field-group">
        {label}
        <input
          id={path}
          className="field-input mono"
          type="url"
          placeholder="https://…"
          {...register(path, { required: isRequired })}
        />
      </div>
    )
  }

  // ── Multi-language text ───────────────────────────────────────────
  if (type === 'lang-string') {
    return (
      <div className="field-group">
        {label}
        <div className="lang-field">
          <Controller
            name={`${path}.__value`}
            control={control}
            defaultValue={''}
            rules={{ required: isRequired }}
            render={({ field }) =>
              longText ? (
                <textarea
                  id={path}
                  className="field-input field-textarea"
                  rows={4}
                  placeholder="Value…"
                  {...field}
                />
              ) : (
                <input
                  id={path}
                  className="field-input"
                  type="text"
                  placeholder="Value…"
                  {...field}
                />
              )
            }
          />
          <Controller
            name={`${path}.__lang`}
            control={control}
            defaultValue={requiresLanguageTag ? 'en' : ''}
            rules={{
              validate: (lang, formValues) => {
                if (!requiresLanguageTag) return true
                const textValue = formValues?.[path]?.__value
                if (!textValue || String(textValue).trim() === '') return true
                return !!lang || 'Language is required'
              },
            }}
            render={({ field }) => (
              <StyledSelect
                inputId={`${path}.__lang`}
                options={[
                  ...(hasNoLanguageOption ? [{ value: '', label: '—' }] : []),
                  ...LANG_OPTIONS.map((l) => ({ value: l, label: languageLabel(l) })),
                ]}
                value={field.value}
                onChange={field.onChange}
                onBlur={field.onBlur}
                selectRef={field.ref}
                placeholder="Lang"
                isClearable={false}
                isSearchable={false}
              />
            )}
          />
        </div>
      </div>
    )
  }

  // ── Default: plain text (Controller ensures reset() works) ────────
  return (
    <div className="field-group">
      {label}
      <Controller
        name={path}
        control={control}
        defaultValue={''}
        rules={{
          required: isRequired,
          pattern: pattern ? new RegExp(pattern) : undefined,
        }}
        render={({ field }) => {
          if (longText) {
            return (
              <textarea id={path} className="field-input field-textarea" rows={4} {...field} />
            )
          }
          const value = String(field.value ?? '').trim()
          const showLink = linkTemplate && value && (!pattern || new RegExp(pattern).test(value))
          return (
            <>
              <input id={path} className="field-input" type="text" {...field} />
              {showLink && (
                <a
                  className="field-external-link"
                  href={externalIdUrl(linkTemplate, value)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open {value} ↗
                </a>
              )}
            </>
          )
        }}
      />
    </div>
  )
}
