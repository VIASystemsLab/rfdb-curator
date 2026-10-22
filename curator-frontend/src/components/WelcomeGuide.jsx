/**
 * First-time curator welcome guide (modal overlay).
 *
 * Shows the project's guide to its data model and the order in which a curator
 * inserts records. The text and the figure come from
 * ../project/WelcomeGuideContent.jsx, the one file a fork replaces; this component
 * is only the dialog. Purely informational — never calls the API, so it is safe
 * under the read-only editor flag.
 *
 * Props:
 *   open     {boolean}   - whether the overlay is visible
 *   onClose  {function}  - called on backdrop click, Escape, or the primary button
 *
 * State (auto-open on first visit, re-openable via a Help button) lives in App.jsx.
 */
import { useEffect, useRef } from 'react'
import Icon from './Icon.jsx'
import {
  GuideDiagram,
  GuideDiagramNote,
  GuideIntro,
  guideSteps,
  guideTitle,
} from '../project/WelcomeGuideContent.jsx'
import './WelcomeGuide.css'

export default function WelcomeGuide({ open, onClose }) {
  const closeRef = useRef(null)

  // Escape closes; focus the close button when the dialog opens.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    closeRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="guide-backdrop" onClick={onClose}>
      <div
        className="guide-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-title"
        // Clicks inside the dialog must not bubble to the backdrop (which closes it).
        onClick={(e) => e.stopPropagation()}
      >
        <button
          ref={closeRef}
          className="guide-close"
          onClick={onClose}
          aria-label="Close guide"
        >
          <Icon name="X" size={18} />
        </button>

        <header className="guide-header">
          <p className="guide-eyebrow">First-time curator guide</p>
          <h2 id="guide-title" className="guide-title">
            {guideTitle}
          </h2>
          <GuideIntro />
        </header>

        <div className="guide-body">
          <div className="guide-diagram-wrap">
            <GuideDiagram />
            <GuideDiagramNote />
          </div>

          <ol className="guide-steps">
            {guideSteps.map((s) => (
              <li key={s.n} className="guide-step">
                <span className="guide-step-num">{s.n}</span>
                <div>
                  <p className="guide-step-title">
                    {s.title} <span className="guide-step-tag">{s.tag}</span>
                  </p>
                  <p className="guide-step-detail">{s.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <footer className="guide-footer">
          <button className="btn btn-primary" onClick={onClose}>
            Got it — start curating
          </button>
        </footer>
      </div>
    </div>
  )
}
