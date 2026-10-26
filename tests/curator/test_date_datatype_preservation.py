"""Date-precision preservation: the editor must keep xsd:gYear / xsd:gYearMonth /
xsd:date exactly as entered and never silently promote a year to a full date.

Two angles are covered:
  * the extractor exposes the ``sh:or`` datatype alternation as a single
    ``temporal`` field carrying all three datatype options; and
  * the write path (JSON-LD -> rdflib -> Turtle) round-trips a year-only value
    with its ``xsd:gYear`` datatype intact.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rdflib import XSD, Graph, Literal, URIRef

from api.data import create_or_update_entity
from core.shacl_validator import ShaclValidator
from models.data import EntityData
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


WORK_SHAPE = "https://rosfeatr.eu/rdf/schema/MusicalWorkShape"
DCTERMS_DATE = URIRef("http://purl.org/dc/terms/date")


class _CapturingStore:
    """Store double: validates via the real SHACL validator and captures the
    Turtle that would be written."""

    def __init__(self) -> None:
        self.loaded_turtle = ""

    def from_clause(self) -> str:
        """Empty SPARQL FROM clause for tests."""
        return ""

    def construct(self, _sparql: str) -> Graph:
        """No pre-existing triples for the payload under test."""
        return Graph()

    def query(self, _sparql: str) -> list:
        """No stored triples: every minted IRI is free."""
        return []

    def load_turtle(self, turtle: str) -> None:
        """Capture the Turtle that would be loaded into the store."""
        self.loaded_turtle = turtle


def _request(store: _CapturingStore) -> SimpleNamespace:
    """Minimal request object expected by create_or_update_entity."""
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=store,
                shape_dep_graph={},
                shacl_validator=ShaclValidator(str(SCHEMA_PATH)),
            )
        )
    )


def _work_payload(date_value: str, date_type: str) -> EntityData:
    """A minimal conforming MusicalWork carrying a single dcterms:date value."""
    return EntityData(
        shapeId=WORK_SHAPE,
        data={
            "@context": {
                "mm": "https://w3id.org/polifonia/ontology/music-meta/",
                "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "dcterms": "http://purl.org/dc/terms/",
                "xsd": "http://www.w3.org/2001/XMLSchema#",
            },
            "@type": ["mm:MusicEntity", "lrmoo:F1_Work"],
            "rdfs:label": {"@value": "Dated Work", "@language": "en"},
            "dcterms:date": {"@value": date_value, "@type": date_type},
        },
        originalTriples=None,
    )


def test_extractor_exposes_temporal_field_with_all_options() -> None:
    """MusicalWork's dcterms:date is one 'temporal' field carrying the three
    permitted datatype options in schema order."""
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    shape = extractor.get_shape(WORK_SHAPE)
    field = next(p for p in shape["properties"] if p["path"] == "dcterms:date")
    assert field["type"] == "temporal"
    assert field["datatypeOptions"] == ["xsd:date", "xsd:gYear", "xsd:gYearMonth"]


def test_year_only_value_keeps_gyear_datatype() -> None:
    """A '1736'^^xsd:gYear value survives the write path without promotion to date."""
    store = _CapturingStore()
    response = create_or_update_entity(_work_payload("1736", "xsd:gYear"), _request(store))
    assert response.success is True
    entity_id = response.entityId

    written = Graph().parse(data=store.loaded_turtle, format="turtle")
    dates = list(written.objects(URIRef(entity_id), DCTERMS_DATE))
    assert len(dates) == 1
    assert isinstance(dates[0], Literal)
    assert dates[0].datatype == XSD.gYear
    assert str(dates[0]) == "1736"


def test_full_date_value_is_accepted_and_preserved() -> None:
    """A full xsd:date value is accepted and kept as xsd:date."""
    store = _CapturingStore()
    response = create_or_update_entity(_work_payload("1736-05-01", "xsd:date"), _request(store))
    assert response.success is True
    entity_id = response.entityId

    written = Graph().parse(data=store.loaded_turtle, format="turtle")
    dates = list(written.objects(URIRef(entity_id), DCTERMS_DATE))
    assert len(dates) == 1
    assert dates[0].datatype == XSD.date
