/**
 * This project's Welcome guide content: the rfdb WEMI model and the order in which
 * a curator inserts records.
 *
 * The one frontend file a fork replaces with its own project text. The dialog
 * itself (WelcomeGuide.jsx) renders whatever this file exports and holds no
 * project text. Keep the export names; the styles come from WelcomeGuide.css.
 *
 * Exports:
 *   guideTitle         {string}     - dialog heading
 *   GuideIntro         {component}  - paragraph under the heading
 *   GuideDiagram       {component}  - figure beside the steps (an inline SVG here)
 *   GuideDiagramNote   {component}  - caption under the figure
 *   guideSteps         {Array}      - numbered steps: {n, tag, title, detail}
 */

export const guideTitle = 'Guide to the Database Structure and Record Insertion'

export function GuideIntro() {
  return (
    <p className="guide-intro">
      Rossiysky Featr follows the <strong>WEMI</strong> model (<a target="_blank" rel="noopener noreferrer" href="https://cidoc-crm.org/lrmoo/short-intro-frbroo">LRMoo</a>).
      For a musical work, records go from the abstract idea down to the physical copy, and every link
      points the other way — from the more concrete record up to its parent. So create parents before children:
      first the auxiliary entities (Place, Person, Subject, Organization, and document Type),
      then the Musical Work (the abstract idea),
      then its Expression (e.g. the libretto text, linked up to the Work via cidoc:P148i_is_component_of),
      then the Manifestation (the published edition, which embodies that Expression),
      and finally each Source (a physical copy held by a library, which exemplifies the Manifestation —
      one Manifestation can have many Sources).
    </p>
  )
}

// The insertion order, one entry per numbered step in the guide.
export const guideSteps = [
  {
    n: 1,
    tag: 'Start here',
    title: 'Supporting entities',
    detail:
      'Create the records other forms point at — Place, Person, Subject, Organization, and document Type. They populate the dropdowns used in later steps.',
  },
  {
    n: 2,
    tag: 'F1 · Work',
    title: 'Musical Work',
    detail:
      'The intellectual creation itself (an opera or composition). Create it first so its Expressions have a parent to point at.',
  },
  {
    n: 3,
    tag: 'F2 · Expression',
    title: 'Expression',
    detail:
      'A specific intellectual realization of the Work — e.g. a Libretto. It links up to its parent Work via cidoc:P148i_is_component_of.',
  },
  {
    n: 4,
    tag: 'F3 · Manifestation',
    title: 'Manifestation',
    detail:
      'The product type — all physical copies of one published edition. It must embody exactly one Expression (lrmoo:R4_embodies), so that Expression has to exist first.',
  },
  {
    n: 5,
    tag: 'F5 · Item',
    title: 'Source',
    detail:
      'A physical copy of a printed work held by a library or archive; it exemplifies a Manifestation (lrmoo:R7_exemplifies) and requires a document Type and a holding Organization. One Manifestation can be exemplified by many Sources. You can attach a digital copy — a scanned PDF — directly from the Source form (cidoc:P138i_has_representation); each is stored as a schema:DigitalDocument. To record a staging, add a Performance (F31), which links to two levels: the Work it performed (lrmoo:R80_performed) and the Manifestation used at / made for it (cidoc:P16_used_specific_object / P19_was_intended_use_of).',
  },
]

/**
 * Inline SVG of the WEMI chain — no charting dependency.
 *
 * Arrows follow each shape's sh:path (subject → object, per schema/rfdb_schema.ttl). With
 * cidoc:P148i_is_component_of the whole WEMI spine flows one way — child → parent (up):
 * Source → Manifestation → Expression → Work. Performance links to two levels.
 */
export function GuideDiagram() {
  const box = (x, y, f, label) => (
    <g>
      <rect x={x} y={y} width="140" height="48" rx="8" className="guide-node" />
      <text x={x + 12} y={y + 20} className="guide-node-tag">
        {f}
      </text>
      <text x={x + 12} y={y + 38} className="guide-node-label">
        {label}
      </text>
    </g>
  )
  // Predicate label: a small prefix line, then the full predicate name(s) below in
  // bigger, bold, lighter type. Pass one name for a single predicate, or several for
  // an edge that stands for more than one (e.g. Performance's P16 / P19).
  const edgeLabel = (x, y, prefix, ...names) => (
    <text x={x} y={y} className="guide-edge-label">
      <tspan x={x} dy="0">
        {prefix}
      </tspan>
      {names.map((name) => (
        <tspan key={name} x={x} dy="12" className="guide-edge-name">
          {name}
        </tspan>
      ))}
    </text>
  )
  return (
    <svg
      className="guide-diagram"
      viewBox="0 0 400 416"
      role="img"
      aria-label="WEMI model with SHACL predicate directions. Every WEMI link points child to parent: Expression (F2) points up to its Musical Work (F1) via cidoc P148i is_component_of; Manifestation (F3) points up to Expression via lrmoo R4 embodies; Source/Item (F5) points up to Manifestation via lrmoo R7 exemplifies. Performance (F31) links to two levels: to the Work via lrmoo R80 performed, and to the Manifestation via cidoc P16 used_specific_object or P19 was_intended_use_of. An optional Digital copy (a schema DigitalDocument, i.e. a scanned PDF), shown at right under Performance, attaches to the Source via cidoc P138i has_representation, drawn as a dotted line to mark it as a stored surrogate rather than a WEMI relation."
    >
      <defs>
        <marker
          id="guide-arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" className="guide-arrowhead" />
        </marker>
      </defs>

      {/* Spine edges — direction matches each shape's sh:path (subject → object). */}
      {/* Expression → Work (up): cidoc:P148i_is_component_of */}
      <line x1="78" y1="128" x2="78" y2="56" className="guide-edge" markerEnd="url(#guide-arrow)" />
      {edgeLabel(86, 80, 'cidoc:', 'P148i_is_component_of')}
      {/* Manifestation → Expression (up): lrmoo:R4_embodies */}
      <line x1="78" y1="248" x2="78" y2="176" className="guide-edge" markerEnd="url(#guide-arrow)" />
      {edgeLabel(86, 200, 'lrmoo:', 'R4_embodies')}
      {/* Source → Manifestation (up): lrmoo:R7_exemplifies */}
      <line x1="78" y1="368" x2="78" y2="296" className="guide-edge" markerEnd="url(#guide-arrow)" />
      {edgeLabel(86, 320, 'lrmoo:', 'R7_exemplifies')}
      {/* Source → Digital copy: cidoc:P138i_has_representation. Dotted (not the
          branch dashes, not the spine solid) — a stored surrogate, not a WEMI
          relation. Right-angle connector into the node's bottom edge. */}
      <polyline
        points="148,392 318,392 318,368"
        className="guide-edge guide-edge--rep"
        fill="none"
        markerEnd="url(#guide-arrow)"
      />
      {edgeLabel(160, 378, 'cidoc:', 'P138i_has_representation')}

      {/* Performance → Work (up): lrmoo:R80_performed — right-angle connector */}
      <polyline
        points="318,176 318,32 148,32"
        className="guide-edge guide-edge--branch"
        fill="none"
        markerEnd="url(#guide-arrow)"
      />
      {edgeLabel(180, 14, 'lrmoo:', 'R80_performed')}
      {/* Performance → Manifestation (down): cidoc:P16 / P19 — right-angle connector */}
      <polyline
        points="318,224 318,272 148,272"
        className="guide-edge guide-edge--branch"
        fill="none"
        markerEnd="url(#guide-arrow)"
      />
      {edgeLabel(172, 280, 'cidoc:', 'P16_used_specific_object', 'P19_was_intended_use_of')}

      {box(8, 8, 'F1 · Work', 'Musical Work')}
      {box(8, 128, 'F2 · Expression', 'Expression')}
      {box(8, 248, 'F3 · Manifestation', 'Manifestation')}
      {box(8, 368, 'F5 · Item', 'Source / Item')}
      {box(248, 176, 'F31 · Performance', 'Performance')}
      {box(248, 320, 'schema · Document', 'Digital copy (PDF)')}
    </svg>
  )
}

export function GuideDiagramNote() {
  return (
    <p className="guide-diagram-note">
      Every WEMI link points the same way — <em>child → parent (up)</em>: a Source points up
      to its Manifestation, a Manifestation up to its Expression, and an Expression up to its Work.
      Performance links to two levels — the Work and the Manifestation.
      A Source may also carry a <em>Digital copy</em> — a scanned PDF attached via
      cidoc:P138i_has_representation (upload it from the Source form); it is drawn with a
      dotted line to mark it as a stored surrogate, not a WEMI relation.
      Supporting entities (Place, Person → Agent Role, Subject) feed these forms via
      their dropdowns.
    </p>
  )
}
