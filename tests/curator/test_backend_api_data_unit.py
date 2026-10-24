"""Unit-level API write coverage for schema-driven data changes."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from rdflib import Graph

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "curator-backend"
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.data import create_or_update_entity  # noqa: E402
from core.shacl_validator import ShaclValidator  # noqa: E402
from models.data import EntityData  # noqa: E402


class _ValidationOxigraph:
    """Oxigraph stub that validates through the real SHACL validator."""

    def __init__(self) -> None:
        self.loaded_turtle = ""

    def from_clause(self) -> str:
        """Return an empty SPARQL FROM clause for tests."""
        return ""

    def construct(self, _sparql: str) -> Graph:
        """Return no existing triples for the payload under test."""
        return Graph()

    def load_turtle(self, turtle: str) -> None:
        """Capture the Turtle payload that would be written to Oxigraph."""
        self.loaded_turtle = turtle


def _request(oxigraph: _ValidationOxigraph):
    """Build the minimal request object expected by create_or_update_entity."""
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=oxigraph,
                shape_dep_graph={},
                shacl_validator=ShaclValidator(str(SCHEMA_PATH)),
            )
        )
    )


def test_api_write_accepts_place_plain_label_and_multiple_same_as() -> None:
    """POST /api/v1/curator/entities accepts an untranslated place label and two sameAs IRIs."""
    oxigraph = _ValidationOxigraph()
    payload = EntityData(
        shapeId="https://rosfeatr.eu/rdf/schema/PlaceShape",
        data={
            "@context": {
                "core": "https://w3id.org/polifonia/ontology/core/",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@id": "https://rosfeatr.eu/rdf/data/api_unit_place",
            "@type": "core:Place",
            "rdfs:label": {"@value": "API Place"},
            "owl:sameAs": [
                {"@id": "https://example.org/place/1"},
                {"@id": "https://example.org/place/2"},
            ],
        },
        originalTriples=None,
    )

    response = create_or_update_entity(payload, _request(oxigraph))

    assert response.success is True
    assert response.validationReport.conforms is True
    assert "owl:sameAs" in oxigraph.loaded_turtle
    assert '"API Place"' in oxigraph.loaded_turtle


def test_api_write_accepts_manifestation_plain_comment() -> None:
    """POST /api/v1/curator/entities accepts an untranslated manifestation comment."""
    oxigraph = _ValidationOxigraph()
    payload = EntityData(
        shapeId="https://rosfeatr.eu/rdf/schema/ManifestationShape",
        data={
            "@context": {
                "cidoc": "http://www.cidoc-crm.org/cidoc-crm/",
                "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
                "mm": "https://w3id.org/polifonia/ontology/music-meta/",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@id": "https://rosfeatr.eu/rdf/data/api_unit_manifestation",
            "@type": "lrmoo:F3_Manifestation",
            "rdfs:label": {"@value": "API Manifestation", "@language": "en"},
            "rdfs:comment": {"@value": "Generic API comment"},
            "lrmoo:R4_embodies": {
                "@id": "https://rosfeatr.eu/rdf/data/api_unit_expression",
                "@type": "lrmoo:F2_Expression",
                "rdfs:label": {"@value": "API Expression", "@language": "en"},
                # Expression → Work via cidoc:P148i_is_component_of (child → parent).
                "cidoc:P148i_is_component_of": {
                    "@id": "https://rosfeatr.eu/rdf/data/api_unit_work",
                    "@type": ["mm:MusicEntity", "lrmoo:F1_Work"],
                    "rdfs:label": {"@value": "API Work", "@language": "en"},
                },
            },
        },
        originalTriples=None,
    )

    response = create_or_update_entity(payload, _request(oxigraph))

    assert response.success is True
    assert response.validationReport.conforms is True
    assert '"Generic API comment"' in oxigraph.loaded_turtle
