"""Tests for the schema-aware graph endpoint: GET /api/v1/dataexplorer/graph/node.

Covers the two things that make the Explorer's traversal meaningful: the
relation-predicate set derived from the SHACL schema (entity links only, not
literals or external-authority IRIs), and the node response assembled from a
CONSTRUCT of the node itself plus grouped inbound/outbound neighbor queries.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from rdflib import Graph

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"
BACKEND_DIR = ROOT / "dataexplorer-backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.graph import relation_predicates, router  # noqa: E402
from rfdb_core.schema_extractor import SchemaExtractor  # noqa: E402

DATA = "https://rosfeatr.eu/rdf/data/"
NODE_URL = "/api/v1/dataexplorer/graph/node"
CORE = "https://w3id.org/polifonia/ontology/core/"

# Known relation predicates (declare sh:node / sh:class in rfdb_schema.ttl).
HAS_AGENT_ROLE = CORE + "hasAgentRole"
IS_COMPONENT_OF = "http://www.cidoc-crm.org/cidoc-crm/P148i_is_component_of"
IS_ABOUT = "http://www.cidoc-crm.org/cidoc-crm/P129_is_about"
DCTERMS_LANGUAGE = "http://purl.org/dc/terms/language"
# Not relations: plain external-authority IRI input, and the label literal.
OWL_SAME_AS = "http://www.w3.org/2002/07/owl#sameAs"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


class _StubOxigraph:
    """Oxigraph double: canned CONSTRUCT graph + branchable SELECT rows."""

    def __init__(self, self_turtle="", out_rows=None, in_rows=None, raise_exc=None):
        self._self_turtle = self_turtle
        self._out_rows = out_rows or []
        self._in_rows = in_rows or []
        self._raise_exc = raise_exc

    def from_clause(self) -> str:
        return ""

    def construct(self, _sparql: str) -> Graph:
        if self._raise_exc is not None:
            raise self._raise_exc
        g = Graph()
        if self._self_turtle.strip():
            g.parse(data=self._self_turtle, format="turtle")
        return g

    def query(self, sparql: str):
        if self._raise_exc is not None:
            raise self._raise_exc
        # Inbound query matches "?s ?p <id>"; outbound matches "<id> ?p ?o".
        return self._in_rows if "?s ?p" in sparql else self._out_rows


def _client(oxigraph) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/dataexplorer")
    app.state.schema_extractor = SchemaExtractor(str(SCHEMA_PATH))
    app.state.store = oxigraph
    return TestClient(app)


# ---------------------------------------------------------------------------
# relation_predicates (schema-derived)
# ---------------------------------------------------------------------------


def test_relation_predicates_include_entity_links() -> None:
    """Predicates that declare sh:node / sh:class are relations."""
    preds = set(relation_predicates(SchemaExtractor(str(SCHEMA_PATH))))
    for pred in (HAS_AGENT_ROLE, IS_COMPONENT_OF, IS_ABOUT, DCTERMS_LANGUAGE):
        assert pred in preds, pred


def test_relation_predicates_exclude_externals_and_literals() -> None:
    """External-authority IRI inputs and literal fields are not relations."""
    preds = set(relation_predicates(SchemaExtractor(str(SCHEMA_PATH))))
    assert OWL_SAME_AS not in preds
    assert RDFS_LABEL not in preds


# ---------------------------------------------------------------------------
# GET /api/v1/dataexplorer/graph/node
# ---------------------------------------------------------------------------

_SOURCE_TURTLE = f"""
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:    <http://www.w3.org/2002/07/owl#> .
@prefix lrmoo:  <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix source: <https://w3id.org/polifonia/ontology/source/> .
@prefix prism:  <http://prismstandard.org/namespaces/basic/2.0/> .
@prefix rfdb:   <{DATA}> .

rfdb:Src1
  a source:Source, lrmoo:F5_Item ;
  rdfs:label "Libretto copy"@en ;
  prism:publicationDate "1736"^^<http://www.w3.org/2001/XMLSchema#gYear> ;
  owl:sameAs <https://www.wikidata.org/entity/Q1> ;
  lrmoo:R7_exemplifies rfdb:Manif1 .
"""


def test_get_node_assembles_self_and_edges() -> None:
    """The response carries the node's own data plus inbound/outbound edges."""
    ox = _StubOxigraph(
        self_turtle=_SOURCE_TURTLE,
        out_rows=[
            {
                "p": "http://iflastandards.info/ns/lrm/lrmoo/R7_exemplifies",
                "o": DATA + "Manif1",
                "label": "First edition",
                "types": "http://x/Manifestation",
            },
        ],
        in_rows=[],
    )
    body = _client(ox).get(NODE_URL, params={"id": DATA + "Src1"}).json()

    assert body["id"] == DATA + "Src1"
    assert body["label"] == "Libretto copy"
    assert "https://w3id.org/polifonia/ontology/source/Source" in body["types"]
    # Literal field kept with datatype; label is not duplicated into literals.
    assert any(lit["datatype"] and lit["value"] == "1736" for lit in body["literals"])
    assert all(lit["predicate"] != RDFS_LABEL for lit in body["literals"])
    # External-authority IRI surfaced separately, not as a graph edge.
    assert body["externalLinks"] == [
        {"predicate": OWL_SAME_AS, "target": "https://www.wikidata.org/entity/Q1"}
    ]
    # Outbound relation edge carries the neighbor's label + types.
    assert len(body["edges"]) == 1
    edge = body["edges"][0]
    assert edge["direction"] == "out"
    assert edge["neighbor"]["id"] == DATA + "Manif1"
    assert edge["neighbor"]["label"] == "First edition"
    assert edge["neighbor"]["types"] == ["http://x/Manifestation"]
    # A small neighbor set is not truncated.
    assert body["truncated"] is False


def test_get_node_includes_inbound_edges() -> None:
    """Inbound edges (who links to this node) are returned with direction 'in'."""
    ox = _StubOxigraph(
        self_turtle=_SOURCE_TURTLE,
        out_rows=[],
        in_rows=[{"p": IS_COMPONENT_OF, "s": DATA + "Expr1", "label": "Libretto", "types": ""}],
    )
    body = _client(ox).get(NODE_URL, params={"id": DATA + "Src1"}).json()
    inbound = [e for e in body["edges"] if e["direction"] == "in"]
    assert len(inbound) == 1
    assert inbound[0]["neighbor"]["id"] == DATA + "Expr1"
    assert inbound[0]["neighbor"]["types"] == []


def test_get_node_caps_neighbors_and_flags_truncation() -> None:
    """More neighbors than `limit` are capped per direction and `truncated` is set."""
    out_rows = [
        {
            "p": "http://iflastandards.info/ns/lrm/lrmoo/R7_exemplifies",
            "o": DATA + f"N{i}",
            "label": f"n{i}",
            "types": "",
        }
        for i in range(5)
    ]
    ox = _StubOxigraph(self_turtle=_SOURCE_TURTLE, out_rows=out_rows, in_rows=[])
    body = _client(ox).get(NODE_URL, params={"id": DATA + "Src1", "limit": 2}).json()
    out_edges = [e for e in body["edges"] if e["direction"] == "out"]
    assert len(out_edges) == 2
    assert body["truncated"] is True


def test_get_node_rejects_bad_iri() -> None:
    """A non-http(s) id is a 400."""
    res = _client(_StubOxigraph()).get(NODE_URL, params={"id": "not-an-iri"})
    assert res.status_code == 400


def test_get_node_404_when_absent() -> None:
    """No triples and no edges → 404."""
    res = _client(_StubOxigraph()).get(NODE_URL, params={"id": DATA + "Nope"})
    assert res.status_code == 404


def test_get_node_503_when_store_down() -> None:
    """A store transport error becomes a 503."""
    ox = _StubOxigraph(raise_exc=RuntimeError("store down"))
    res = _client(ox).get(NODE_URL, params={"id": DATA + "Src1"})
    assert res.status_code == 503
