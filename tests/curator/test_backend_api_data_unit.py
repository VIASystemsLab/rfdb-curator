"""Unit-level API write coverage for schema-driven data changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rdflib import Graph

from api.data import create_or_update_entity
from core.shacl_validator import ShaclValidator
from models.data import EntityData

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


class _ValidationOxigraph:
    """Oxigraph stub that validates through the real SHACL validator.

    ``referenced`` is Turtle for the records a payload cites: what the validation
    merge would fetch from a real store.
    """

    def __init__(self, referenced: str = "") -> None:
        self.loaded_turtle = ""
        self.referenced = Graph().parse(data=referenced, format="turtle")

    def from_clause(self) -> str:
        """Return an empty SPARQL FROM clause for tests."""
        return ""

    def construct(self, _sparql: str) -> Graph:
        """Return the cited records, whatever the query."""
        return self.referenced

    def query(self, _sparql: str) -> list:
        """No stored triples: every minted IRI is free."""
        return []

    def load_turtle(self, turtle: str) -> None:
        """Capture the Turtle payload that would be written to Oxigraph."""
        self.loaded_turtle = turtle


# The Expression a Manifestation embodies, and the Work it is part of
# (Expression → Work via cidoc:P148i_is_component_of, child → parent).
_STORED_EXPRESSION = """
@prefix cidoc: <http://www.cidoc-crm.org/cidoc-crm/> .
@prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix mm: <https://w3id.org/polifonia/ontology/music-meta/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rfdb: <https://rosfeatr.eu/rdf/data/> .

rfdb:api_unit_expression a lrmoo:F2_Expression ;
  rdfs:label "API Expression"@en ;
  cidoc:P148i_is_component_of rfdb:api_unit_work .

rfdb:api_unit_work a mm:MusicEntity, lrmoo:F1_Work ;
  rdfs:label "API Work"@en .
"""


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
    oxigraph = _ValidationOxigraph(_STORED_EXPRESSION)
    payload = EntityData(
        shapeId="https://rosfeatr.eu/rdf/schema/ManifestationShape",
        data={
            "@context": {
                "cidoc": "http://www.cidoc-crm.org/cidoc-crm/",
                "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
                "mm": "https://w3id.org/polifonia/ontology/music-meta/",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@type": "lrmoo:F3_Manifestation",
            "rdfs:label": {"@value": "API Manifestation", "@language": "en"},
            "rdfs:comment": {"@value": "Generic API comment"},
            "lrmoo:R4_embodies": {"@id": "https://rosfeatr.eu/rdf/data/api_unit_expression"},
        },
        originalTriples=None,
    )

    response = create_or_update_entity(payload, _request(oxigraph))

    assert response.success is True
    assert response.validationReport.conforms is True
    assert '"Generic API comment"' in oxigraph.loaded_turtle
