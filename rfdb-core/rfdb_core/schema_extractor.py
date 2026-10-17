"""Schema extraction layer: parse a SHACL Turtle file and expose shape metadata
as plain Python dicts consumable by FastAPI route handlers and the frontend.

Overview
--------
The application's data model is defined entirely in a SHACL schema (schema.ttl).
This module reads that file once at startup, parses every ``sh:NodeShape``, and
produces structured descriptors that drive two things:

  1. **Form generation** — the frontend fetches ``GET /api/v1/curator/forms?shapeId=...`` and
     receives a list of field descriptors.  Each descriptor carries enough information
     for the React form layer to pick the right input widget (text, enum, entity
     search, nested editor, language-tagged list, etc.) without any hardcoded field
     knowledge.

  2. **Entity listing** — the reader's ``entities`` and ``entities/counts`` routes use
     ``targetClassUri`` to build SPARQL queries scoped to the right RDF class.

Polymorphic type selection
--------------------------
A shape may declare ``sh:or`` directly on the shape node, with every branch a
single ``sh:class`` constraint (e.g. ``ContributorShape``: Person | Organization
under ``sh:targetClass foaf:Agent``). ``_extract_type_options()`` detects this
pattern and returns it as ``typeOptions``, so the frontend can offer a "which
concrete type is this?" selector at creation time. The chosen value is emitted
alongside ``targetClass`` in the entity's ``@type`` array — ``targetClass`` alone
never satisfies the ``sh:or`` constraint, and it must stay in ``@type`` too so
entity-listing queries (which match ``targetClassUri`` exactly) keep finding it.

Shape roles
-----------
Every NodeShape is classified into one of two roles by ``_infer_shape_role()``:

  ``standalone-entity``
      The shape has an ``rdfs:label`` property — it models a first-class named
      entity (Person, Place, Work, …) that can be created and searched independently.

  ``helper-bridge``
      The shape has no ``rdfs:label`` — it is a pure relation container (e.g.
      AgentRole) that only makes sense inline as part of a parent entity.  The
      frontend renders these via ``AnonymousEntityEditor`` and blocks direct
      top-level editing.

Field type resolution
---------------------
``_infer_field_type()`` maps SHACL constraints to a frontend widget type string.
The resolution runs in priority order:

  ``enum``            sh:in list present
  ``lang-string``     datatype rdf:langString  (single-valued)
  ``temporal``        datatype xsd:date / xsd:gYear / xsd:gYearMonth
  ``year``            datatype xsd:gYear (single, not mixed)
  ``number``          datatype xsd:decimal / xsd:integer / xsd:int
  ``text``            any other explicit datatype
  ``nested``          sh:node pointing to a helper-bridge shape, or sh:nodeKind BlankNode
  ``entity-search``   sh:class or sh:node pointing to a standalone-entity shape
  ``uri``             sh:nodeKind IRI without a class constraint
  ``text``            fallback
  ``lang-string-list`` lang-string + maxCount absent or > 1  (post-processing step)
  ``file-list``       sh:node whose shape targets schema:DigitalDocument
                      (post-processing step)

The ``lang-string-list`` and ``file-list`` overrides are applied as post-processing
steps in ``_extract_property()`` after the base type is resolved, so
``_infer_field_type()`` remains a pure constraint-to-type mapper with no
cardinality awareness.

Field descriptors
-----------------
Every field descriptor includes both ``path`` (compact CURIE, used as the
react-hook-form field key) and ``pathUri`` (full URI, used on the frontend to
match dirty fields against ``record.triples[].predicate`` when building the
``originalTriples`` payload for targeted SPARQL DELETE on update).

Caching
-------
Parsed shapes are cached in ``_cache`` after the first access.  The schema file
is read exactly once per process lifetime.  To pick up schema changes a process
restart is required.
"""

from __future__ import annotations

from typing import Any

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS, SH, XSD

from rfdb_core.prefixes import declared_prefixes
from rfdb_core.vocab import SCHEMA_DIGITAL_DOCUMENT as _SCHEMA_DIGITAL_DOCUMENT

# Predicates whose values are free-form prose and should render as a multi-line
# textarea rather than a single-line input. SHACL has no native "long text" hint,
# so we derive it from the property's sh:path: these are the description / note /
# transcription predicates used across the schema. Extend this set to opt another
# predicate into the larger textarea widget.
LONG_TEXT_PREDICATES: frozenset[str] = frozenset(
    {
        str(RDFS.comment),  # rdfs:comment — free-text notes / descriptions
        "http://purl.org/dc/terms/description",  # dcterms:description
        "http://purl.org/dc/terms/abstract",  # dcterms:abstract
        "http://www.w3.org/2004/02/skos/core#definition",  # skos:definition
        "http://www.w3.org/2004/02/skos/core#note",  # skos:note
        "http://www.w3.org/2004/02/skos/core#scopeNote",  # skos:scopeNote
        "https://w3id.org/polifonia/ontology/core/text",  # core:text — title-page transcription
    }
)


def _english_first(literals: list[Literal]) -> Literal | None:
    """Pick the English literal, else an untagged one, else any; None if empty.

    Labels and names come in several languages (``"RISM ID"@en``,
    ``"Identificativo RISM"@it``), and ``Graph.value`` would return an arbitrary one.
    """
    en = next((lit for lit in literals if (lit.language or "").lower() == "en"), None)
    if en is not None:
        return en
    no_lang = next((lit for lit in literals if not lit.language), None)
    if no_lang is not None:
        return no_lang
    return literals[0] if literals else None


def _curie(uri: URIRef | BNode | None, graph: Graph) -> str | None:
    """Convert a full URI to a CURIE (prefix:local) using the graph's namespace manager.

    Returns the full URI string if no matching prefix is found, or None if uri is None.
    """
    if uri is None:
        return None
    uri_str = str(uri)
    for prefix, ns in graph.namespaces():
        ns_str = str(ns)
        if uri_str.startswith(ns_str) and prefix:
            return f"{prefix}:{uri_str[len(ns_str) :]}"
    return uri_str


class SchemaExtractor:
    """Parse schema.ttl and expose SHACL NodeShape metadata as Python dicts.

    Results are cached after the first parse so the schema file is only read once
    per process lifetime.  Call `get_all_shapes()` or `get_shape(id)` to access
    the parsed metadata.
    """

    def __init__(self, schema_path: str) -> None:
        self.graph = Graph()
        self.graph.parse(schema_path, format="turtle")
        # rdflib pre-binds 'schema' to https://schema.org/ (TLS variant), which
        # collides with the schema.ttl @prefix (http://) and mangles CURIEs to
        # 'schema1:'. Rebind explicitly so compaction matches the schema file.
        self.graph.bind("schema", "http://schema.org/", override=True, replace=True)
        # The CURIE prefix map the schema declares (sh:declare). Read here so a
        # conflicting declaration fails at load, like a Turtle syntax error does.
        self.prefixes: dict[str, str] = declared_prefixes(self.graph)
        self._cache: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all_shapes(self) -> list[dict[str, Any]]:
        """Return all NodeShape descriptors as a list of dicts."""
        return list(self._shapes().values())

    def get_shape(self, shape_id: str) -> dict[str, Any] | None:
        """Return the descriptor for a single NodeShape by its full URI string, or None."""
        return self._shapes().get(shape_id)

    def file_links(self) -> list[tuple[str, str]]:
        """Find every property that links an entity to an uploaded file.

        A link is a property the form renders as a file upload (``file-list``:
        its ``sh:node`` shape targets ``schema:DigitalDocument``). Using the same
        rule as the form means the upload widget and the file lifecycle (the
        download route, the stats, the cleanup script) cannot disagree, whatever
        the upload shape is called. Several upload shapes, or several classes
        linking to one, are simply more links.

        Returns:
            Sorted, deduplicated ``[(parent_target_class_uri, link_predicate_uri), …]``;
            empty when the schema declares no file upload.
        """
        links = {
            (shape["targetClassUri"], prop["pathUri"])
            for shape in self.get_all_shapes()
            for prop in shape.get("properties", [])
            if prop.get("type") == "file-list" and shape.get("targetClassUri")
        }
        return sorted(links)

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    def _shapes(self) -> dict[str, dict[str, Any]]:
        """Build and cache the shape registry on first access."""
        if self._cache is None:
            self._cache = {}
            for shape_uri in self.graph.subjects(RDF.type, SH.NodeShape):
                self._cache[str(shape_uri)] = self._extract_shape(shape_uri)
        return self._cache

    def _extract_shape(self, shape_uri: URIRef) -> dict[str, Any]:
        """Extract all relevant metadata for a single NodeShape."""
        g = self.graph
        label = self._preferred_label(shape_uri) or _curie(shape_uri, g)
        description = g.value(shape_uri, SH.description) or g.value(shape_uri, RDFS.comment) or ""
        target_class = g.value(shape_uri, SH.targetClass)
        shape_role = self._infer_shape_role(shape_uri)

        # Collect any additional sh:class declarations on the shape itself
        additional_types = [
            curie
            for klass in g.objects(shape_uri, SH["class"])
            if (curie := _curie(klass, g)) is not None
        ]

        # Collect polymorphic type alternatives from a shape-level sh:or whose
        # every branch is a single sh:class constraint (e.g. ContributorShape:
        # sh:or ( [sh:class foaf:Person] [sh:class foaf:Organization] ) ). The
        # frontend uses this to offer a "which concrete type is this?" selector
        # at creation time, since sh:targetClass alone is too generic to satisfy
        # the sh:or constraint on its own.
        type_options = self._extract_type_options(shape_uri)

        # Extract and filter out None results from property extraction
        properties = [
            prop
            for prop_node in g.objects(shape_uri, SH.property)
            if (prop := self._extract_property(prop_node)) is not None
        ]

        return {
            "id": str(shape_uri),
            "label": str(label),
            "description": str(description),
            "targetClass": _curie(target_class, g),
            "targetClassUri": str(target_class) if target_class else None,
            "shapeRole": shape_role,
            "additionalTypes": additional_types,
            "typeOptions": type_options,
            "properties": properties,
        }

    def _extract_type_options(self, shape_uri: URIRef) -> list[dict[str, str]]:
        """Return concrete-type choices from a shape-level sh:or/sh:class alternation.

        Only matches when every branch of the sh:or list is exactly one
        sh:class constraint (no other constraint components). Returns an
        empty list for shapes without this pattern, or for property-level
        sh:or (e.g. datatype alternation), which this never inspects.
        """
        g = self.graph
        or_list_head = g.value(shape_uri, SH["or"])
        if or_list_head is None:
            return []

        options: list[dict[str, str]] = []
        for branch in Collection(g, or_list_head):
            branch_triples = list(g.predicate_objects(branch))
            classes = [o for p, o in branch_triples if p == SH["class"]]
            if len(branch_triples) != 1 or len(classes) != 1:
                return []
            curie = _curie(classes[0], g)
            if curie is None:
                return []
            options.append({"value": curie, "label": curie.split(":")[-1]})
        return options

    def _extract_property(self, prop_node: BNode | URIRef) -> dict[str, Any] | None:
        """Extract metadata for a single sh:property node.

        Returns None if the property has no sh:path (malformed shape).

        Field type resolution:
          1. _infer_field_type() derives the base type from datatype, nodeKind, etc.
          2. A post-processing step promotes 'lang-string' to 'lang-string-list' when
             sh:maxCount is absent or > 1, so the frontend renders a multi-value list
             editor (LangStringList) instead of a single input.
        """
        g = self.graph
        path = g.value(prop_node, SH.path)
        if path is None:
            return None

        name = _english_first([o for o in g.objects(prop_node, SH.name) if isinstance(o, Literal)])
        description = g.value(prop_node, SH.description) or ""
        datatype = g.value(prop_node, SH.datatype)

        # Collect datatype options from sh:or lists (e.g. xsd:gYear | xsd:gYearMonth | xsd:date)
        datatype_options: list[URIRef] = []
        or_list_head = g.value(prop_node, SH["or"])
        if or_list_head:
            for option in Collection(g, or_list_head):
                option_datatype = g.value(option, SH.datatype)
                if isinstance(option_datatype, URIRef):
                    datatype_options.append(option_datatype)

        min_count = g.value(prop_node, SH.minCount)
        max_count = g.value(prop_node, SH.maxCount)
        node_kind = g.value(prop_node, SH.nodeKind)
        klass = g.value(prop_node, SH["class"])
        node = g.value(prop_node, SH.node)
        pattern = g.value(prop_node, SH.pattern)

        # Collect sh:in enumeration values
        in_values: list[str] = []
        in_list_head = g.value(prop_node, SH["in"])
        if in_list_head:
            in_values = [str(v) for v in Collection(g, in_list_head)]

        path_curie = _curie(path, g) or str(path)
        # Compute datatype CURIE for the return dict before field_type resolution
        datatype_curie = _curie(datatype, g)
        nested_shape_role = self._infer_nested_shape_role(node)

        # Step 1: derive the base field type from shape constraints
        field_type = self._infer_field_type(
            datatype,
            datatype_options,
            node_kind,
            klass,
            node,
            in_values,
            nested_shape_role,
        )

        # Step 2: promote lang-string to lang-string-list for multi-valued fields.
        # A field is multi-valued when sh:maxCount is absent (unbounded) or explicitly > 1.
        # Nested fields are excluded — they are managed by AnonymousEntityEditor, not
        # the LangStringList widget.
        if (
            field_type == "lang-string"
            and (max_count is None or (str(max_count).isdigit() and int(max_count) > 1))
            and nested_shape_role is None
        ):
            field_type = "lang-string-list"

        # Step 3: properties whose nested shape targets schema:DigitalDocument are
        # file uploads — the node is machine-filled by the upload-first flow
        # (api/files.py), not hand-edited, so the frontend renders a file widget
        # instead of a bridge-node editor. Schema-driven: ANY shape declaring such
        # a property gets the widget with no code change.
        if node is not None and str(g.value(node, SH.targetClass) or "") == str(
            _SCHEMA_DIGITAL_DOCUMENT
        ):
            field_type = "file-list"

        datatype_uris = [str(datatype)] if datatype else []
        datatype_uris.extend(str(dt) for dt in datatype_options)
        has_lang_string = str(RDF.langString) in datatype_uris
        has_plain_string = str(XSD.string) in datatype_uris

        language_tag_policy = "not-applicable"
        if field_type in {"lang-string", "lang-string-list"}:
            if has_lang_string and has_plain_string:
                language_tag_policy = "optional"
            elif has_lang_string:
                language_tag_policy = "required"

        return {
            "path": path_curie,  # compact CURIE used as form field key
            "pathUri": str(path),  # full URI used to match against record.triples[].predicate
            "name": str(name) if name else path_curie.split(":")[-1],
            "description": str(description),
            "type": field_type,
            "longText": str(path) in LONG_TEXT_PREDICATES,
            "datatype": datatype_curie,
            "datatypeOptions": [_curie(dt, g) for dt in datatype_options],
            "languageTagPolicy": language_tag_policy,
            "nodeKind": _curie(node_kind, g),
            "nodeClass": _curie(klass, g),
            "nestedShape": str(node) if node else None,
            "nestedShapeRole": nested_shape_role,
            "minCount": int(min_count) if min_count is not None else 0,
            "maxCount": int(max_count) if max_count is not None else None,  # None means unbounded
            "pattern": str(pattern) if pattern else None,
            "in": in_values,
        }

    def _preferred_label(self, shape_uri: URIRef) -> Literal | None:
        """Return the best available label for a shape, preferring English.

        Priority: rdfs:label (en) > rdfs:label (no lang) > rdfs:label (any) >
                  sh:name (en) > sh:name (no lang) > sh:name (any) > None
        """
        g = self.graph
        labels = [o for o in g.objects(shape_uri, RDFS.label) if isinstance(o, Literal)]
        if labels:
            return _english_first(labels)
        return _english_first([o for o in g.objects(shape_uri, SH.name) if isinstance(o, Literal)])

    def _infer_nested_shape_role(self, nested_shape: URIRef | None) -> str | None:
        """Return the shape role of a nested shape, or None if no nested shape."""
        if nested_shape is None:
            return None
        return self._infer_shape_role(nested_shape)

    def _infer_shape_role(self, shape_uri: URIRef) -> str:
        """Classify a shape as 'standalone-entity' or 'helper-bridge'.

        Policy: a shape that declares an rdfs:label property is a standalone entity
        (it has its own identity and label).  A shape without rdfs:label is a
        helper/bridge node that only exists to connect other entities.
        """
        for prop_node in self.graph.objects(shape_uri, SH.property):
            path = self.graph.value(prop_node, SH.path)
            if path == RDFS.label:
                return "standalone-entity"
        return "helper-bridge"

    def _infer_field_type(
        self,
        datatype: URIRef | None,
        datatype_options: list[URIRef],
        node_kind: URIRef | None,
        klass: URIRef | None,
        node: URIRef | None,
        in_values: list[str],
        nested_shape_role: str | None = None,
    ) -> str:
        """Map SHACL constraints to a frontend field type string.

        Resolution order:
          1. sh:in present              → 'enum'
          2. datatype is rdf:langString → 'lang-string'
             (may be promoted to 'lang-string-list' by caller)
          3. datatype is temporal       → 'temporal'
          4. datatype is xsd:gYear      → 'year'
          5. datatype is numeric        → 'number'
          6. any other datatype         → 'text'
          7. sh:node + BlankNode/helper → 'nested'
          8. sh:class or sh:node        → 'entity-search'
          9. sh:nodeKind IRI            → 'uri'
         10. fallback                   → 'text'

        The caller (``_extract_property``) may then override the result:
        'lang-string' becomes 'lang-string-list' when multi-valued, and any
        sh:node whose shape targets schema:DigitalDocument becomes 'file-list'.
        """
        # Enum: sh:in list present
        if in_values:
            return "enum"

        # Build a unified pool of datatype URIs to test against
        datatype_pool = [str(datatype)] if datatype else []
        datatype_pool.extend(str(dt) for dt in datatype_options)

        if datatype_pool:
            # Language-tagged string
            if any(dt == str(RDF.langString) or "langString" in dt for dt in datatype_pool):
                return "lang-string"

            # Temporal: date / gYear / gYearMonth (only when all options are temporal)
            temporal_types = {str(XSD.date), str(XSD.gYear), str(XSD.gYearMonth)}
            if any(dt in temporal_types for dt in datatype_pool):
                if set(datatype_pool).issubset(temporal_types):
                    return "temporal"

            # Year-only (single xsd:gYear without mixed temporal options)
            if str(XSD.gYear) in datatype_pool:
                return "year"

            # Numeric
            if any(
                dt in (str(XSD.decimal), str(XSD.integer), str(XSD.int)) for dt in datatype_pool
            ):
                return "number"

            # All other explicit datatypes (xsd:string, xsd:anyURI, etc.)
            return "text"

        # No datatype — resolve from node kind and shape references
        nk = str(node_kind) if node_kind else ""

        if node:
            # Blank node or helper-bridge nested shape → inline nested editor
            if "BlankNode" in nk or nested_shape_role == "helper-bridge":
                return "nested"

        # Standalone entity reference via sh:class or sh:node
        if klass or node:
            return "entity-search"

        # Plain IRI without class constraint → external URI input
        if "IRI" in nk:
            return "uri"

        return "text"
