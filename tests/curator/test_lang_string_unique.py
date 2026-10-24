"""Language-tagged literals and sh:uniqueLang.

Covers what the editor promises for language-tagged fields:
  * a language tag entered on a value is preserved through the write path;
  * a field that also allows xsd:string accepts an untagged value;
  * ``sh:uniqueLang`` is declared where the schema intends a single-language
    label (SourceShape's title); and
  * a plain ``rdf:langString`` field (skos:altLabel) accepts several languages.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pyshacl import validate
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS, SH

from api.data import create_or_update_entity
from core.shacl_validator import ShaclValidator
from models.data import EntityData
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


PLACE_SHAPE = "https://rosfeatr.eu/rdf/schema/PlaceShape"
SOURCE_SHAPE_URI = URIRef("https://rosfeatr.eu/rdf/schema/SourceShape")

_PREFIXES = """
@prefix core: <https://w3id.org/polifonia/ontology/core/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix rfdb: <https://rosfeatr.eu/rdf/data/> .
"""


class _CapturingOxigraph:
    """Oxigraph double: validates via the real SHACL validator and captures the
    Turtle that would be written."""

    def __init__(self) -> None:
        self.loaded_turtle = ""

    def from_clause(self) -> str:
        """Empty SPARQL FROM clause for tests."""
        return ""

    def construct(self, _sparql: str) -> Graph:
        """No pre-existing triples for the payload under test."""
        return Graph()

    def load_turtle(self, turtle: str) -> None:
        """Capture the Turtle that would be loaded into Oxigraph."""
        self.loaded_turtle = turtle


def _request(oxigraph: _CapturingOxigraph) -> SimpleNamespace:
    """Minimal request object expected by create_or_update_entity."""
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=oxigraph,
                shape_dep_graph={},
                shacl_validator=ShaclValidator(str(SCHEMA_PATH)),
            )
        )
    )


def _place_label_payload(entity_id: str, label: dict) -> EntityData:
    """A Place carrying a single rdfs:label value (tagged or plain)."""
    return EntityData(
        shapeId=PLACE_SHAPE,
        data={
            "@context": {
                "core": "https://w3id.org/polifonia/ontology/core/",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@id": entity_id,
            "@type": "core:Place",
            "rdfs:label": label,
        },
        originalTriples=None,
    )


def _conforms(body: str) -> bool:
    """Validate a Turtle snippet against the project schema; return conformance."""
    conforms, _, _ = validate(
        data_graph=Graph().parse(data=_PREFIXES + body, format="turtle"),
        shacl_graph=Graph().parse(str(SCHEMA_PATH), format="turtle"),
        inference="rdfs",
        advanced=True,
    )
    return conforms


def test_language_tag_is_preserved_through_write() -> None:
    """A rdf:langString value keeps its language tag end to end."""
    entity_id = "https://rosfeatr.eu/rdf/data/lang_place"
    oxigraph = _CapturingOxigraph()
    response = create_or_update_entity(
        _place_label_payload(entity_id, {"@value": "Верона", "@language": "ru"}),
        _request(oxigraph),
    )
    assert response.success is True

    written = Graph().parse(data=oxigraph.loaded_turtle, format="turtle")
    labels = list(written.objects(URIRef(entity_id), RDFS.label))
    assert len(labels) == 1
    assert isinstance(labels[0], Literal)
    assert labels[0].language == "ru"
    assert str(labels[0]) == "Верона"


def test_untagged_value_is_accepted_when_string_allowed() -> None:
    """The label field allows xsd:string, so an untagged value is valid and stays
    untagged."""
    entity_id = "https://rosfeatr.eu/rdf/data/plain_place"
    oxigraph = _CapturingOxigraph()
    response = create_or_update_entity(
        _place_label_payload(entity_id, {"@value": "Verona"}), _request(oxigraph)
    )
    assert response.success is True

    written = Graph().parse(data=oxigraph.loaded_turtle, format="turtle")
    labels = list(written.objects(URIRef(entity_id), RDFS.label))
    assert len(labels) == 1
    assert labels[0].language is None


def test_source_label_declares_unique_lang() -> None:
    """SourceShape's rdfs:label carries sh:uniqueLang true (single-language title)."""
    schema = Graph().parse(str(SCHEMA_PATH), format="turtle")
    label_props = [
        prop
        for prop in schema.objects(SOURCE_SHAPE_URI, SH.property)
        if schema.value(prop, SH.path) == RDFS.label
    ]
    assert len(label_props) == 1
    assert schema.value(label_props[0], SH.uniqueLang) == Literal(True)


def test_multilingual_altlabels_conform() -> None:
    """skos:altLabel (rdf:langString, no uniqueLang) accepts several languages."""
    assert _conforms(
        'rfdb:P a core:Place ; rdfs:label "Verona"@it ; skos:altLabel "Verona"@en, "Vérone"@fr .\n'
    )


def test_label_field_language_tag_policy_is_optional() -> None:
    """The extractor reports the label field as language-optional (langString|string)."""
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    shape = extractor.get_shape(PLACE_SHAPE)
    field = next(p for p in shape["properties"] if p["path"] == "rdfs:label")
    assert field["languageTagPolicy"] == "optional"
