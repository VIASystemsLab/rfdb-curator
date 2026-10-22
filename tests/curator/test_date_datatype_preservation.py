"""Date-precision preservation: the editor must keep xsd:gYear / xsd:gYearMonth /
xsd:date exactly as entered and never silently promote a year to a full date.

Two angles are covered:
  * the extractor exposes the ``sh:or`` datatype alternation as a single
    ``temporal`` field carrying all three datatype options; and
  * the write path (JSON-LD -> rdflib -> Turtle) round-trips a year-only value
    with its ``xsd:gYear`` datatype intact.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from rdflib import XSD, Graph, Literal, URIRef

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"
BACKEND_DIR = ROOT / "curator-backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.data import create_or_update_entity  # noqa: E402
from core.shacl_validator import ShaclValidator  # noqa: E402
from models.data import EntityData  # noqa: E402
from rfdb_core.schema_extractor import SchemaExtractor  # noqa: E402

WORK_SHAPE = "https://rosfeatr.eu/rdf/schema/MusicalWorkShape"
DCTERMS_DATE = URIRef("http://purl.org/dc/terms/date")


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


def _work_payload(entity_id: str, date_value: str, date_type: str) -> EntityData:
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
            "@id": entity_id,
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
    entity_id = "https://rosfeatr.eu/rdf/data/gyear_work"
    oxigraph = _CapturingOxigraph()
    response = create_or_update_entity(
        _work_payload(entity_id, "1736", "xsd:gYear"), _request(oxigraph)
    )
    assert response.success is True

    written = Graph().parse(data=oxigraph.loaded_turtle, format="turtle")
    dates = list(written.objects(URIRef(entity_id), DCTERMS_DATE))
    assert len(dates) == 1
    assert isinstance(dates[0], Literal)
    assert dates[0].datatype == XSD.gYear
    assert str(dates[0]) == "1736"


def test_full_date_value_is_accepted_and_preserved() -> None:
    """A full xsd:date value is accepted and kept as xsd:date."""
    entity_id = "https://rosfeatr.eu/rdf/data/date_work"
    oxigraph = _CapturingOxigraph()
    response = create_or_update_entity(
        _work_payload(entity_id, "1736-05-01", "xsd:date"), _request(oxigraph)
    )
    assert response.success is True

    written = Graph().parse(data=oxigraph.loaded_turtle, format="turtle")
    dates = list(written.objects(URIRef(entity_id), DCTERMS_DATE))
    assert len(dates) == 1
    assert dates[0].datatype == XSD.date
