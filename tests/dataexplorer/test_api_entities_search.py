"""Tests for shape resolution in GET /api/v1/dataexplorer/entities/search.

``?shape=`` takes the full shape URI or its exact local name. A suffix match
would resolve "RoleShape" to whichever of RoleShape and AgentRoleShape comes
first in rfdb_schema.ttl, so these tests run with the shapes in both orders.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rdflib import RDF, RDFS, Graph, Literal, URIRef

from api.entities import router
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


SEARCH_URL = "/api/v1/dataexplorer/entities/search"
SCHEMA_NS = "https://rosfeatr.eu/rdf/schema/"
CORE = "https://w3id.org/polifonia/ontology/core/"


PLACE = "https://w3id.org/polifonia/ontology/core/Place"
TRICKY_LABEL = 'Teatro (Nuovo) "x" a.b'
SKOS_ALT_LABEL = URIRef("http://www.w3.org/2004/02/skos/core#altLabel")
SCHEMA_ALTERNATE_NAME = URIRef("http://schema.org/alternateName")


class _GraphStore:
    """A store answering from a real rdflib graph holding one Place with a tricky label."""

    def __init__(self) -> None:
        self.graph = Graph()
        place = URIRef("https://rosfeatr.eu/rdf/data/P1")
        self.graph.add((place, RDF.type, URIRef(PLACE)))
        self.graph.add((place, RDFS.label, Literal(TRICKY_LABEL)))

    def from_clause(self) -> str:
        return ""

    def query(self, sparql: str) -> list[dict]:
        return [{k: str(v) for k, v in row.asdict().items()} for row in self.graph.query(sparql)]


class _RenamedPlaceStore(_GraphStore):
    """Adds a Place known by other names: two SKOS alternative labels and a schema.org one."""

    def __init__(self) -> None:
        super().__init__()
        place = URIRef("https://rosfeatr.eu/rdf/data/P2")
        self.graph.add((place, RDF.type, URIRef(PLACE)))
        self.graph.add((place, RDFS.label, Literal("Sankt-Peterburg")))
        self.graph.add((place, SKOS_ALT_LABEL, Literal("Petrograd")))
        self.graph.add((place, SKOS_ALT_LABEL, Literal("Leningrad")))
        self.graph.add((place, SCHEMA_ALTERNATE_NAME, Literal("Piter")))


class _CapturingStore:
    """Store double that records the SPARQL it is asked and returns no rows."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def from_clause(self) -> str:
        return ""

    def query(self, sparql: str) -> list:
        self.queries.append(sparql)
        return []


def _client(store, *, reverse: bool = False) -> TestClient:
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    if reverse:
        shapes = list(reversed(extractor.get_all_shapes()))
        extractor.get_all_shapes = lambda: shapes
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/dataexplorer")
    app.state.schema_extractor = extractor
    app.state.store = store
    return TestClient(app)


@pytest.mark.parametrize("reverse", [False, True])
def test_local_name_resolves_exactly_whatever_the_shape_order(reverse: bool) -> None:
    """Resolve an exact local shape name correctly regardless of shape order."""
    store = _CapturingStore()
    response = _client(store, reverse=reverse).get(SEARCH_URL, params={"shape": "RoleShape"})

    assert response.status_code == 200
    assert f"a <{CORE}Role>" in store.queries[0]
    assert f"a <{CORE}AgentRole>" not in store.queries[0]


def test_full_shape_uri_resolves() -> None:
    store = _CapturingStore()
    response = _client(store).get(SEARCH_URL, params={"shape": SCHEMA_NS + "AgentRoleShape"})

    assert response.status_code == 200
    assert f"a <{CORE}AgentRole>" in store.queries[0]


@pytest.mark.parametrize("shape", ["Shape", "leShape", "schema/RoleShape"])
def test_partial_name_is_not_found(shape: str) -> None:
    store = _CapturingStore()
    response = _client(store).get(SEARCH_URL, params={"shape": shape})

    assert response.status_code == 404
    assert store.queries == []


def test_search_text_with_regex_metacharacters_matches_literally() -> None:
    """A ``(`` typed into a relation field used to make the regex invalid: no suggestions."""
    client = _client(_GraphStore())
    for query in ("(Nuovo", '"x"', "a.b"):
        response = client.get(SEARCH_URL, params={"shape": "PlaceShape", "query": query})
        assert response.status_code == 200
        assert [hit["label"] for hit in response.json()] == [TRICKY_LABEL], query
    response = client.get(SEARCH_URL, params={"shape": "PlaceShape", "query": "a?b"})
    assert response.json() == []  # "?" is not a wildcard either


def test_store_failure_is_logged(caplog) -> None:
    """No suggestions when the store is down, and a warning saying so."""

    class _DownStore(_CapturingStore):
        def query(self, sparql: str) -> list:
            raise RuntimeError("store unreachable")

    with caplog.at_level(logging.WARNING, logger="api.entities"):
        response = _client(_DownStore()).get(SEARCH_URL, params={"shape": "PlaceShape"})

    assert response.status_code == 200
    assert response.json() == []
    assert any("store unreachable" in r.getMessage() for r in caplog.records)


def _search(client: TestClient, query: str) -> list[str]:
    response = client.get(SEARCH_URL, params={"shape": "PlaceShape", "query": query})
    assert response.status_code == 200
    return [hit["label"] for hit in response.json()]


def _client_with_label_predicates(monkeypatch, predicates: list[str]) -> TestClient:
    """A client whose search reads ``predicates`` as SEARCH_LABEL_PREDICATES."""
    from core.config import settings

    monkeypatch.setattr(settings, "search_label_predicates", predicates)
    return _client(_RenamedPlaceStore())


def test_an_alternative_label_matches_and_the_primary_label_is_shown(monkeypatch) -> None:
    """Typing a former name finds the record, listed once under its rdfs:label."""
    client = _client_with_label_predicates(monkeypatch, [str(SKOS_ALT_LABEL)])

    assert _search(client, "petrograd") == ["Sankt-Peterburg"]
    assert _search(client, "grad") == ["Sankt-Peterburg"]  # two alt labels match, one row
    assert _search(client, "Sankt") == ["Sankt-Peterburg"]  # the label still matches


def test_the_setting_decides_which_predicates_are_searched(monkeypatch) -> None:
    """Only the listed predicates count; an empty list searches the label and IRI only."""
    client = _client_with_label_predicates(monkeypatch, [str(SCHEMA_ALTERNATE_NAME)])
    assert _search(client, "Piter") == ["Sankt-Peterburg"]
    assert _search(client, "Petrograd") == []

    client = _client_with_label_predicates(monkeypatch, [])
    assert _search(client, "Piter") == []
    assert _search(client, "Sankt") == ["Sankt-Peterburg"]
