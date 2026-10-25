"""Read routes for data: list, counts, fetch one.

The emphasis here is the invariant the read-only deploy mode depends on: **a read
service pointed at an empty or unseeded store must return empty results, not
500s.** The read-only deploy mode brings the reader up with no writer around to
seed, so "nothing there yet" has to be an ordinary answer rather than an outage.

Handlers are called directly with a stubbed store, matching the other unit-level
suites.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rdflib import RDF, RDFS, Graph, Literal, URIRef

from api.data import count_data_by_shape, get_entity, list_data
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


_EXTRACTOR = SchemaExtractor(str(SCHEMA_PATH))
PLACE_SHAPE = "https://rosfeatr.eu/rdf/schema/PlaceShape"


PLACE = "https://w3id.org/polifonia/ontology/core/Place"
TRICKY_LABEL = 'Teatro (Nuovo) "x" a.b'


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


class _EmptyStore:
    """A reachable store with nothing in it — a freshly created, unseeded store."""

    def from_clause(self) -> str:
        return ""

    def query(self, _sparql: str) -> list[dict]:
        return []

    def construct(self, _sparql: str) -> Graph:
        return Graph()


class _DownStore:
    """A store that raises on every call — Oxigraph unreachable."""

    def from_clause(self) -> str:
        return ""

    def query(self, _sparql: str):
        raise RuntimeError("store unreachable")

    def construct(self, _sparql: str):
        raise RuntimeError("store unreachable")


def _request(store):
    """A request object shaped like the reader's app.state."""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(store=store, schema_extractor=_EXTRACTOR))
    )


# ---------------------------------------------------------------------------
# Empty store: empty answers, never errors (a fresh read-mode instance starts empty)
# ---------------------------------------------------------------------------


def test_list_on_empty_store_returns_empty_page():
    result = list_data(_request(_EmptyStore()), shapeId=PLACE_SHAPE, q="", limit=50, offset=0)
    assert result.shapeId == PLACE_SHAPE
    assert result.total == 0
    assert result.items == []


def test_counts_on_empty_store_returns_zero_for_every_shape():
    counts = count_data_by_shape(_request(_EmptyStore()))["counts"]
    assert counts, "the schema defines shapes, so the map must not be empty"
    assert set(counts.values()) == {0}


def test_get_entity_on_empty_store_is_404_not_500():
    """Absent is 404 — a read of something that does not exist, not a failure."""
    with pytest.raises(HTTPException) as exc:
        get_entity(_request(_EmptyStore()), entity_id="https://rosfeatr.eu/rdf/data/Nope")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Unreachable store
# ---------------------------------------------------------------------------


def test_list_degrades_to_an_empty_page_when_the_store_is_down():
    """The record list stays renderable: the sidebar shows nothing, not an error."""
    result = list_data(_request(_DownStore()), shapeId=PLACE_SHAPE, q="", limit=50, offset=0)
    assert result.total == 0
    assert result.items == []


def test_counts_degrade_to_zeros_when_the_store_is_down():
    counts = count_data_by_shape(_request(_DownStore()))["counts"]
    assert set(counts.values()) == {0}


def test_degrading_to_empty_is_logged(caplog):
    """Empty answers keep the sidebar usable, but an operator must see why."""
    with caplog.at_level(logging.WARNING, logger="api.data"):
        list_data(_request(_DownStore()), shapeId=PLACE_SHAPE, q="", limit=50, offset=0)
        count_data_by_shape(_request(_DownStore()))
    messages = [r.getMessage() for r in caplog.records]
    assert any("Listing shape" in m and "store unreachable" in m for m in messages)
    assert any("Counting shape" in m and "store unreachable" in m for m in messages)


def test_get_entity_reports_503_when_the_store_is_down():
    """A single-entity fetch cannot fake an answer, so it surfaces the outage."""
    with pytest.raises(HTTPException) as exc:
        get_entity(_request(_DownStore()), entity_id="https://rosfeatr.eu/rdf/data/L111")
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_unknown_shape_is_404():
    with pytest.raises(HTTPException) as exc:
        list_data(_request(_EmptyStore()), shapeId="urn:not:a:shape", q="", limit=50, offset=0)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "bad_iri",
    [
        "urn:isbn:123",  # not http(s)
        "https://rosfeatr.eu/rdf/data/A B",  # whitespace
        "https://rosfeatr.eu/rdf/data/X> . }",  # would close the SPARQL <…>
    ],
)
def test_unsafe_iri_is_rejected_before_it_reaches_the_store(bad_iri):
    """The guard runs before any query is built, so a bad IRI never interpolates."""

    class _Exploding(_EmptyStore):
        def construct(self, _sparql):
            raise AssertionError("the IRI guard must reject before querying")

    with pytest.raises(HTTPException) as exc:
        get_entity(_request(_Exploding()), entity_id=bad_iri)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Search text
# ---------------------------------------------------------------------------


def test_search_text_with_regex_metacharacters_matches_literally():
    """A ``(`` in the search box used to make the regex invalid, so the list came back empty."""
    store = _GraphStore()
    for q in ("(Nuovo", '"x"', "a.b"):
        result = list_data(_request(store), shapeId=PLACE_SHAPE, q=q, limit=50, offset=0)
        assert [item.label for item in result.items] == [TRICKY_LABEL], q
        assert result.total == 1, q
    result = list_data(_request(store), shapeId=PLACE_SHAPE, q="a?b", limit=50, offset=0)
    assert result.items == []  # "?" is not a wildcard either
