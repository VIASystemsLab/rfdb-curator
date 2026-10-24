"""Schema and form metadata coverage for editor-facing RDF constraints."""

from __future__ import annotations

from pathlib import Path

import pytest
from pyshacl import validate
from rdflib import Graph

from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


def _assert_conforms(data_graph: Graph) -> None:
    """Assert that a graph conforms to the project SHACL schema."""
    schema_graph = Graph().parse(str(SCHEMA_PATH), format="turtle")
    conforms, _, report_text = validate(
        data_graph=data_graph,
        shacl_graph=schema_graph,
        inference="rdfs",
        advanced=True,
        abort_on_first=False,
    )
    assert conforms, report_text


def _schema_field(shape_id: str, path: str) -> dict:
    """Return the extracted field descriptor for a shape path."""
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    shape = extractor.get_shape(shape_id)
    assert shape is not None
    field = next((prop for prop in shape["properties"] if prop["path"] == path), None)
    assert field is not None
    return field


@pytest.mark.parametrize(
    "shape_id",
    [
        "https://rosfeatr.eu/rdf/schema/PlaceShape",
        "https://rosfeatr.eu/rdf/schema/MusicalWorkShape",
        "https://rosfeatr.eu/rdf/schema/PersonShape",
        "https://rosfeatr.eu/rdf/schema/HoldingOrganizationShape",
    ],
)
def test_owl_same_as_allows_multiple_values(shape_id: str) -> None:
    """owl:sameAs is unbounded for external authority fields."""
    field = _schema_field(shape_id, "owl:sameAs")

    assert field["maxCount"] is None
    assert field["nodeKind"] == "sh:IRI"
    assert field["type"] == "uri"


def test_place_plain_label_with_multiple_same_as_conforms() -> None:
    """A place accepts an untranslated label and multiple external authorities."""
    data_graph = Graph()
    data_graph.parse(
        data="""
@prefix core: <https://w3id.org/polifonia/ontology/core/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rfdb: <https://rosfeatr.eu/rdf/data/> .

rfdb:TestPlace
  a core:Place ;
  rdfs:label "Test Place" ;
  owl:sameAs <https://example.org/place/1>, <https://example.org/place/2> .
""",
        format="turtle",
    )

    _assert_conforms(data_graph)


def test_manifestation_plain_comment_conforms() -> None:
    """A manifestation accepts an untranslated rdfs:comment literal."""
    data_graph = Graph()
    data_graph.parse(
        data="""
@prefix cidoc: <http://www.cidoc-crm.org/cidoc-crm/> .
@prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix mm:    <https://w3id.org/polifonia/ontology/music-meta/> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rfdb:  <https://rosfeatr.eu/rdf/data/> .

rfdb:TestManifestation
  a lrmoo:F3_Manifestation ;
  rdfs:label "Test Manifestation"@en ;
  rdfs:comment "Generic comment" ;
  lrmoo:R4_embodies rfdb:TestExpression .

rfdb:TestExpression
  a lrmoo:F2_Expression ;
  rdfs:label "Test Expression"@en ;
  cidoc:P148i_is_component_of rfdb:TestWork .

rfdb:TestWork
  a mm:MusicEntity, lrmoo:F1_Work ;
  rdfs:label "Test Work"@en .
""",
        format="turtle",
    )

    _assert_conforms(data_graph)


def test_label_field_is_language_capable_with_string_option() -> None:
    """Label fields remain language-capable while accepting plain strings."""
    field = _schema_field("https://rosfeatr.eu/rdf/schema/PlaceShape", "rdfs:label")

    assert field["type"] == "lang-string"
    assert field["datatypeOptions"] == ["rdf:langString", "xsd:string"]


def test_language_shape_exists_with_correct_target_class() -> None:
    """rfdbs:LanguageShape exists and targets dcterms:LinguisticSystem."""
    from rdflib import URIRef
    from rdflib.namespace import SH

    schema = Graph().parse(str(SCHEMA_PATH), format="turtle")
    shape_uri = URIRef("https://rosfeatr.eu/rdf/schema/LanguageShape")
    target = schema.value(shape_uri, SH.targetClass)
    assert target == URIRef("http://purl.org/dc/terms/LinguisticSystem"), (
        f"Expected dcterms:LinguisticSystem, got {target}"
    )


def test_source_shape_language_field_is_entity_search() -> None:
    """dcterms:language in SourceShape resolves to entity-search with correct class."""
    field = _schema_field("https://rosfeatr.eu/rdf/schema/SourceShape", "dcterms:language")
    assert field["type"] == "entity-search", f"Expected entity-search, got {field['type']}"
    assert field["nodeClass"] == "dcterms:LinguisticSystem", (
        f"Expected dcterms:LinguisticSystem, got {field['nodeClass']}"
    )
    assert field["nestedShape"] == "https://rosfeatr.eu/rdf/schema/LanguageShape", (
        f"Expected rfdbs:LanguageShape, got {field['nestedShape']}"
    )


def test_contributor_shape_exposes_type_options() -> None:
    """ContributorShape's shape-level sh:or/sh:class alternation surfaces as typeOptions.

    ContributorShape targets foaf:Agent but every focus node must also satisfy
    `sh:or ( [sh:class foaf:Person] [sh:class foaf:Organization] )`. targetClass
    alone can never satisfy that, so the extractor exposes the alternatives as
    typeOptions for the frontend to offer as a "which concrete type?" selector.
    """
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    shape = extractor.get_shape("https://rosfeatr.eu/rdf/schema/ContributorShape")
    assert shape is not None
    assert shape["targetClass"] == "foaf:Agent"
    assert shape["typeOptions"] == [
        {"value": "foaf:Person", "label": "Person"},
        {"value": "foaf:Organization", "label": "Organization"},
    ]


def test_only_contributor_shape_declares_type_options() -> None:
    """No other shape accidentally matches the sh:or/sh:class alternation pattern.

    Every other shape's sh:or usage in rfdb_schema.ttl is a
    property-level datatype alternation (e.g. rdf:langString | xsd:string),
    not a shape-level sh:class alternation. If a future schema edit adds a
    shape-level sh:or with mixed constraints, this should stay empty; only a
    clean sh:class-only alternation should ever populate typeOptions.
    """
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    shapes_with_options = [
        shape["id"] for shape in extractor.get_all_shapes() if shape["typeOptions"]
    ]
    assert shapes_with_options == ["https://rosfeatr.eu/rdf/schema/ContributorShape"]
