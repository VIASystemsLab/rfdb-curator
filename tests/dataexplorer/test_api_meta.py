"""Unit tests for the reader's meta routes: meta/schema, meta/prefixes and meta/graphs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dataexplorer-backend"
SCHEMA_PATH = REPO_ROOT / "schema" / "schema.ttl"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

META = "/api/v1/dataexplorer/meta"
SCHEMA_URL = f"{META}/schema"
PREFIXES_URL = f"{META}/prefixes"
GRAPHS_URL = f"{META}/graphs"


def _make_meta_app() -> FastAPI:
    """Build a minimal FastAPI app with just the meta router mounted at /api/v1/dataexplorer."""
    from api.meta import router
    from rfdb_core.schema_extractor import SchemaExtractor

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/dataexplorer")
    app.state.schema_extractor = SchemaExtractor(str(SCHEMA_PATH))
    return app


# ---------------------------------------------------------------------------
# GET /api/v1/dataexplorer/meta/prefixes
# ---------------------------------------------------------------------------


def test_get_schema_reports_the_loaded_version() -> None:
    """meta/schema serves the version the loaded schema states about itself."""
    from rfdb_core.schema_extractor import SchemaExtractor

    res = TestClient(_make_meta_app()).get(SCHEMA_URL)
    assert res.status_code == 200
    assert res.json() == {"version": SchemaExtractor(str(SCHEMA_PATH)).version}
    assert res.json()["version"]


def test_get_prefixes_returns_200_with_prefixes_key() -> None:
    """Response is 200 and top-level JSON has a 'prefixes' key."""
    res = TestClient(_make_meta_app()).get(PREFIXES_URL)
    assert res.status_code == 200
    assert "prefixes" in res.json()


def test_get_prefixes_covers_all_ttl_sources() -> None:
    """The schema's sh:declare covers schema, data-only, and glottolog-only prefixes."""
    prefixes = TestClient(_make_meta_app()).get(PREFIXES_URL).json()["prefixes"]
    assert prefixes["rfdb"] == "https://rosfeatr.eu/rdf/data/"  # schema.ttl
    assert prefixes["lrmoo"] == "http://iflastandards.info/ns/lrm/lrmoo/"  # schema.ttl
    assert prefixes["wd"] == "http://www.wikidata.org/entity/"  # data.ttl only
    assert prefixes["lexvo"] == "http://lexvo.org/ontology#"  # glottolog only


def test_get_prefixes_excludes_rdflib_default_noise() -> None:
    """rdflib's auto-bound vocabularies must not leak into the declared map."""
    prefixes = TestClient(_make_meta_app()).get(PREFIXES_URL).json()["prefixes"]
    for noise in ("brick", "csvw", "dcat", "qb", "odrl", "prov", "doap"):
        assert noise not in prefixes, f"unexpected rdflib default '{noise}' in map"
    assert "" not in prefixes


def test_scan_ttl_prefixes_reads_header_and_stops_at_triples(tmp_path: Path) -> None:
    """scan_ttl_prefixes captures @prefix lines, skips the base/empty prefix, and
    stops once the first triple is reached (ignoring later directives)."""
    from rfdb_core.prefixes import scan_ttl_prefixes

    ttl = tmp_path / "sample.ttl"
    ttl.write_text(
        "# comment\n"
        "@prefix ex: <http://example.org/> .\n"
        "@prefix : <http://example.org/base/> .\n"  # empty prefix — skipped
        "@base <http://example.org/> .\n"
        "\n"
        "ex:Thing a ex:Class .\n"
        "@prefix late: <http://example.org/late/> .\n",  # after a triple — ignored
        encoding="utf-8",
    )
    assert scan_ttl_prefixes([str(ttl)]) == {"ex": "http://example.org/"}


# ---------------------------------------------------------------------------
# GET /api/v1/dataexplorer/meta/graphs
# ---------------------------------------------------------------------------

GRAPH_A = "https://rosfeatr.eu/rdf/graph/"
GRAPH_B = "https://rosfeatr.eu/rdf/vocab/"


class _StubOxigraph:
    """Minimal Oxigraph double that routes the three meta/graphs queries by content.

    ``named_rows`` is a list of per-graph dicts with string cells:
    ``{"g", "count", "subjects", "objects", "literals"}``. The stub reshapes them
    into the count/distinct grouped query and the literals grouped query, and
    answers the default-graph count separately.
    """

    def __init__(self, named_rows, default_count="0", totals=None, raise_exc=None):
        self._named_rows = named_rows
        self._default_count = default_count
        self._totals = totals or {"subjects": "0", "objects": "0", "literals": "0"}
        self._raise_exc = raise_exc

    def query(self, sparql: str):
        if self._raise_exc is not None:
            raise self._raise_exc
        grouped = "GROUP BY ?g" in sparql
        if "isLiteral" in sparql:
            if grouped:  # per-graph distinct literals
                return [{"g": r["g"], "literals": r.get("literals", "0")} for r in self._named_rows]
            return [{"literals": self._totals["literals"]}]  # store-wide distinct literals
        if "COUNT(DISTINCT ?s)" in sparql:
            if grouped:  # per-graph count + distinct subjects/objects
                return [
                    {
                        "g": r["g"],
                        "count": r.get("count", "0"),
                        "subjects": r.get("subjects", "0"),
                        "objects": r.get("objects", "0"),
                    }
                    for r in self._named_rows
                ]
            return [  # store-wide distinct subjects/objects
                {"subjects": self._totals["subjects"], "objects": self._totals["objects"]}
            ]
        return [{"count": self._default_count}]  # default-graph triple count


def _make_graphs_app(oxigraph) -> FastAPI:
    """Build a minimal FastAPI app with the meta router and a stub store."""
    from api.meta import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/dataexplorer")
    app.state.store = oxigraph
    return app


def _set_active_graph(monkeypatch: pytest.MonkeyPatch, value) -> None:
    """Point settings.data_graph_uri at ``value`` for the duration of a test."""
    from core.config import settings

    monkeypatch.setattr(settings, "data_graph_uri", value)


def test_get_graphs_returns_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Response is 200 with exactly the documented top-level keys."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph([{"g": GRAPH_A, "count": "1234"}, {"g": GRAPH_B, "count": "87"}])
    res = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL)
    assert res.status_code == 200
    assert set(res.json()) == {
        "activeGraph",
        "graphs",
        "totalTriples",
        "totalSubjects",
        "totalObjects",
        "totalLiterals",
        "warnings",
    }


def test_get_graphs_marks_active_sorts_and_casts(monkeypatch: pytest.MonkeyPatch) -> None:
    """graphs[] is sorted by URI, counts are ints, and the active graph is flagged once."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph([{"g": GRAPH_B, "count": "87"}, {"g": GRAPH_A, "count": "1234"}])
    body = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL).json()

    assert body["activeGraph"] == GRAPH_A
    assert [g["uri"] for g in body["graphs"]] == sorted([GRAPH_A, GRAPH_B])
    active = [g for g in body["graphs"] if g["active"]]
    assert len(active) == 1
    assert active[0]["uri"] == GRAPH_A
    assert active[0]["count"] == 1234
    assert not body["warnings"]  # active graph present + non-empty, default empty


def test_get_graphs_reports_per_graph_distinct_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each graph row carries its own distinct subject/object/literal counts as ints."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph(
        [{"g": GRAPH_A, "count": "10", "subjects": "7", "objects": "12", "literals": "5"}]
    )
    body = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL).json()
    row = body["graphs"][0]
    assert row["count"] == 10
    assert row["subjects"] == 7
    assert row["objects"] == 12
    assert row["literals"] == 5


def test_get_graphs_totals_are_store_wide_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Footer totals use store-wide distinct counts, not the sum of per-graph columns."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph(
        [
            {"g": GRAPH_A, "count": "10", "subjects": "7", "objects": "12", "literals": "5"},
            {"g": GRAPH_B, "count": "4", "subjects": "3", "objects": "5", "literals": "2"},
        ],
        totals={"subjects": "8", "objects": "15", "literals": "6"},
    )
    body = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL).json()
    # Deduplicated store-wide totals are below the per-graph column sums (10, 17, 7).
    assert body["totalSubjects"] == 8
    assert body["totalObjects"] == 15
    assert body["totalLiterals"] == 6


def test_total_triples_sums_named_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """totalTriples is the sum of every named-graph count plus the default graph."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph(
        [{"g": GRAPH_A, "count": "1234"}, {"g": GRAPH_B, "count": "87"}],
        default_count="5",
    )
    body = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL).json()
    assert body["totalTriples"] == 1234 + 87 + 5


def test_warning_when_active_graph_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured active graph missing from the store surfaces a warning."""
    _set_active_graph(monkeypatch, "https://rosfeatr.eu/rdf/unused/")
    ox = _StubOxigraph([{"g": GRAPH_A, "count": "1234"}])
    body = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL).json()
    assert any("empty or absent" in w for w in body["warnings"])


def test_warning_when_default_graph_populated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Triples in the default graph (invisible to the scoped editor) warn."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph([{"g": GRAPH_A, "count": "10"}], default_count="3")
    body = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL).json()
    assert any("default graph" in w for w in body["warnings"])


def test_get_graphs_returns_503_when_store_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store transport error becomes a 503 (matching api/data.py), not a 500."""
    _set_active_graph(monkeypatch, GRAPH_A)
    ox = _StubOxigraph([], raise_exc=RuntimeError("store down"))
    res = TestClient(_make_graphs_app(ox)).get(GRAPHS_URL)
    assert res.status_code == 503
