# RFDB Curator — Data Model Contract

What a SHACL schema must provide for this application to run on it. The record
types, fields, vocabularies and modelling choices all come from the loaded
schema. This page lists only what the application itself relies on.

The schema this repository ships is the rfdb model, maintained upstream in
[rfdb-schema](https://github.com/VIASystemsLab/rfdb-schema). Its general
modelling rules are in upstream's
[application-rules.md](https://github.com/VIASystemsLab/rfdb-schema/blob/v2.2.2/docs/application-rules.md)
and are not repeated here: model alignment, explicit `@type`, literal, language
and date policies, IRI rules, and naming across layers. For how shapes become
forms and how validation runs, see [architecture.md](architecture.md).

---

## The loaded schema is the source of truth

Both services parse the schema at `SCHEMA_PATH` at startup. Forms, validation,
autocomplete, the record-type list and export are all derived from it, so a
field or a prefix is defined there and nowhere else.

This repository's [`schema/rfdb_schema.ttl`](../schema/rfdb_schema.ttl) is a copy of a
tagged upstream release, recorded in [`schema/README.md`](../schema/README.md).
It changes only by copying a newer release; a model change goes upstream. To run
another model, point `SCHEMA_PATH` at it (see [custom-schema.md](custom-schema.md)).

---

## Designed around WEMI

The editor is designed around a layered Work → Expression → Manifestation →
Item model. Links point from child to parent, so parents are created before
children: a child's form needs an existing parent to reference.

The code does not enforce this. No service reads an LRMoo term, and a schema
with another structure works. The one place that assumes it is the Welcome
guide's text in the curator frontend.

---

## Standalone entities and helper nodes

The role of each shape is derived from the schema, never from its name:

- **Standalone entity**: a shape with an `rdfs:label` property. It has its own
  identity, is listed, searched and created as a top-level record.
- **Helper/bridge node**: a shape without one. It exists only inline in its
  parent (for example an agent-role node binding a person to a role), and the
  editor blocks editing it on its own.

See [architecture.md](architecture.md#helperbridge-shape-classification) for how
this drives the UI.

---

## Class-targeted shapes need explicit `@type`

Shapes select their nodes with `sh:targetClass`, so a node is validated only if
it declares the class. The editor writes `@type` on every node it creates,
bridge nodes included. Data loaded from elsewhere must do the same, or its
constraints are silently skipped. The validation-graph merge that lets a record
validate against existing related records is in
[architecture.md](architecture.md).

---

## Shape constraints the editor understands

Each property shape becomes one form field. The widget follows from its
constraints, checked in this order:

| Constraint | Widget |
|---|---|
| `sh:in` | choice list |
| `sh:datatype rdf:langString` | text with a language tag; a repeatable list when `sh:maxCount` is absent or above 1 |
| `sh:datatype` among `xsd:date`, `xsd:gYear`, `xsd:gYearMonth` (directly or in a datatype `sh:or`) | date with a chosen precision; `xsd:gYear` alone is a year input |
| `sh:datatype` `xsd:decimal`, `xsd:integer` or `xsd:int` | number |
| any other `sh:datatype` | text |
| `sh:node` pointing at a helper shape, or `sh:nodeKind sh:BlankNode` | inline nested editor |
| `sh:node` whose shape targets `schema:DigitalDocument` | file upload (see below) |
| `sh:class`, or `sh:node` pointing at a standalone shape | search over existing records |
| `sh:nodeKind sh:IRI` without a class | IRI input |

Other conventions:

- **Type choice.** A shape-level `sh:or` whose branches are each one
  `sh:class` becomes a type selector on the form (for example person or
  organization).
- **Language tags.** The tag is kept through JSON-LD and RDF export, and
  `sh:uniqueLang true` is enforced per language.
- **Date precision.** A year-only or year-month value is stored as entered,
  never promoted to a full date.
- **Long text.** SHACL has no long-text hint, so the predicates listed in the
  `LONG_TEXT_PREDICATES` setting get a multi-line input. Any other text field
  gets a single line and saves the same way.
- **Field names.** `sh:name` gives the label, English first, then untagged,
  then any language.

---

## Identifiers

- New records are named `<DATA_BASE_URI><ShapeName>_<8 hex>`, for example
  `…/data/Place_4f3a9d12`.
- Helper nodes are named nodes, not blank nodes, so they survive updates:
  `<DATA_BASE_URI><parent>_<property>_<index>`.
- Shapes are looked up under `SCHEMA_BASE_URI`.
- Existing IRIs are never changed. An IRI that is not `http(s)` or contains
  characters unsafe in SPARQL is rejected before it is stored.

Both base IRIs are settings; [custom-schema.md](custom-schema.md) explains them.

---

## Vocabularies the application relies on

- **Structural:** `rdf`, `rdfs`, `owl`, `xsd`, `sh`. `rdfs:label` marks
  standalone entities and supplies record labels.
- **File uploads:** `schema:` (see below).
- **Long text:** whatever the `LONG_TEXT_PREDICATES` setting lists (see above).

No other ontology term is read by name. Everything else comes from the schema.

---

## Controlled vocabularies and reference data

Some records are reference data, browsed and selected rather than authored. They
are ordinary shapes whose records are seeded from the files in `VOCAB_PATH`.
`READ_ONLY_SHAPES` lists the shapes whose records cannot be created, edited or
deleted. Seeding and reset are in the
[deployment guide](deployment.md#data-seeding).

---

## Digital documents

Uploads need one shape targeting `schema:DigitalDocument`, under any name, linked
by `sh:node` from each class that takes uploads. Several such links, or several
upload shapes, are fine. The upload endpoint fills in `schema:name`,
`schema:encodingFormat`, `schema:contentUrl`, `schema:contentSize`,
`schema:sha256` and `schema:numberOfPages`. Without such a shape, uploads are
off, and the cleanup script refuses to run if file nodes or stored files exist.

---

## Schema metadata

- **Prefixes:** every prefix the schema and the data files use is declared with
  `sh:declare`. The services read their CURIE map from those declarations only.
- **Version:** `owl:versionInfo` on the schema states its release. The services
  log it at startup and serve it on `GET /api/v1/dataexplorer/meta/schema`.

---

## The schema this repository runs

The rfdb schema, at the release recorded in
[`schema/README.md`](../schema/README.md). It reuses these vocabularies:

| Prefix | Role in the rfdb model |
|---|---|
| `lrmoo:` | The Work / Expression / Manifestation / Item spine and its links. |
| `cidoc:` | Cultural-heritage relations LRMoo does not cover: subjects, custody, performance evidence. |
| `core:` | Polifonia Core: people, roles, places, organizations, and the agent-role bridge. |
| `source:` | Polifonia Source: a physical or documentary copy held by an institution. |
| `mm:` | Polifonia Music Meta: the musical work type. |
| `dcterms:` | Dates, languages, identifiers, and digital-copy contributors. |
| `prism:` | Publication dates. |
| `foaf:` | Donor and provenance agents, kept apart from creative agents. |
| `schema:` | Digital-copy metadata. |
| `skos:` | Preferred and alternate labels. |

Its modelling decisions and their rationale are in upstream's
[data-model.md](https://github.com/VIASystemsLab/rfdb-schema/blob/v2.2.2/docs/data-model.md),
and its external identifiers (VIAF, RISM) in
[external-identifiers.md](https://github.com/VIASystemsLab/rfdb-schema/blob/v2.2.2/docs/external-identifiers.md).
