"""Conformance suite for the ``TripleStore`` seam.

Every ``TripleStore`` implementation must pass this suite unchanged. Any future
implementation (Fuseki/Jena, GraphDB, Qlever, RDF4J, an embedded library) should
be added to ``STORE_FACTORIES``.

Three tiers, deliberately:

1. **Structural** — the implementation satisfies the Protocol.
2. **Graph scoping** — ``from_clause``/``with_clause``/``graph`` are pure string
   logic, so they are checked exactly, with and without a configured graph. These
   are contract, not implementation detail: handlers interpolate them into SPARQL
   and ``validation_merge`` takes ``from_clause`` as a parameter.
3. **Transport** — request shape and response parsing, against a stubbed HTTP layer
   rather than a live store, so the suite runs in CI where no store exists. The
   live round-trip at the bottom skips when nothing is listening.

What is deliberately *not* asserted: the exact SPARQL a caller passes. These
methods are pure transport — they send the string as given — and the routes own
their queries.
"""

from __future__ import annotations

import socket

import pytest
from rdflib import Graph

from rfdb_core.triplestore import OxigraphStore, TripleStore, build_triplestore
from rfdb_core.triplestore import oxigraph as oxigraph_module

GRAPH = "https://rosfeatr.eu/rdf/graph/"

# Every implementation of the seam, keyed by its TRIPLESTORE setting value. Add a
# new store here and the whole suite below applies to it.
STORE_FACTORIES = {"oxigraph": OxigraphStore}


# ---------------------------------------------------------------------------
# A stub HTTP layer, so the transport tier needs no live store
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, *, text: str = "", json_body=None, status_code: int = 200):
        self.text = text
        self._json = json_body
        self.status_code = status_code

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


class _StubClient:
    """Records every request and replays a queued response.

    Instances are handed out by :func:`_patch_http`, which swaps out the module's
    ``httpx.Client``. ``calls`` accumulates ``(method, url, kwargs)`` so tests can
    assert the request shape — which endpoint, which params — without a network.
    """

    calls: list[tuple[str, str, dict]] = []
    response = _StubResponse()

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        type(self).calls.append(("POST", url, kwargs))
        return type(self).response

    def get(self, url, **kwargs):
        type(self).calls.append(("GET", url, kwargs))
        return type(self).response


@pytest.fixture
def http(monkeypatch):
    """Swap the store module's httpx.Client for the recording stub."""
    _StubClient.calls = []
    _StubClient.response = _StubResponse()
    monkeypatch.setattr(oxigraph_module.httpx, "Client", _StubClient)
    return _StubClient


@pytest.fixture(params=sorted(STORE_FACTORIES))
def store_cls(request):
    """Every registered implementation, so each test runs against all of them."""
    return STORE_FACTORIES[request.param]


# ---------------------------------------------------------------------------
# 1. Structural conformance
# ---------------------------------------------------------------------------


def test_implementation_satisfies_the_protocol(store_cls) -> None:
    """An instance is recognised as a TripleStore.

    Only proves the members exist — the Protocol is runtime_checkable, which does
    not verify signatures. The tiers below cover behaviour.
    """
    assert isinstance(store_cls("http://localhost:7878", GRAPH), TripleStore)


def test_protocol_covers_every_method_callers_use(store_cls) -> None:
    """Guard against a member being dropped from the seam by accident.

    Each name below has real call sites in the routes, the seeder, or the cleanup
    script, so losing one is a silent break rather than an import error.
    """
    expected = {
        "graph",
        "from_clause",
        "with_clause",
        "query",
        "construct",
        "health",
        "update",
        "load_turtle",
        "clear_store",
    }
    assert expected <= set(dir(store_cls))


# ---------------------------------------------------------------------------
# 2. Graph scoping
# ---------------------------------------------------------------------------


def test_clauses_scope_to_the_configured_graph(store_cls) -> None:
    """With a graph set, the clauses name it verbatim."""
    store = store_cls("http://localhost:7878", GRAPH)
    assert store.graph == GRAPH
    assert store.from_clause() == f"FROM <{GRAPH}>"
    assert store.with_clause() == f"WITH <{GRAPH}>"


@pytest.mark.parametrize("unset", [None, ""])
def test_clauses_are_empty_without_a_graph(store_cls, unset) -> None:
    """With no graph, the clauses are empty strings — never the literal "None".

    ``/api/v1/dataexplorer/meta/graphs`` depends on this: it queries unscoped so the Data Context
    Panel can see every named graph, and interpolating a stray clause would silently
    narrow those counts.
    """
    store = store_cls("http://localhost:7878", unset)
    assert store.graph == unset
    assert store.from_clause() == ""
    assert store.with_clause() == ""


def test_base_url_trailing_slash_is_normalised(store_cls) -> None:
    """A trailing slash on the endpoint never reaches the request URL."""
    assert store_cls("http://localhost:7878/", GRAPH).base_url == "http://localhost:7878"


# ---------------------------------------------------------------------------
# 3. Transport: request shape and response parsing
# ---------------------------------------------------------------------------


def test_query_returns_one_dict_per_solution(store_cls, http) -> None:
    """SELECT rows become {variable: value} dicts, with unbound variables as None."""
    http.response = _StubResponse(
        json_body={
            "head": {"vars": ["s", "label"]},
            "results": {
                "bindings": [
                    {"s": {"value": "urn:a"}, "label": {"value": "A"}},
                    {"s": {"value": "urn:b"}},  # label unbound in this solution
                ]
            },
        }
    )
    rows = store_cls("http://localhost:7878", GRAPH).query("SELECT ?s ?label WHERE {}")

    assert rows == [{"s": "urn:a", "label": "A"}, {"s": "urn:b", "label": None}]
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("POST", "http://localhost:7878/query")
    assert kwargs["headers"]["Accept"] == "application/sparql-results+json"


def test_query_rejects_a_malformed_result_body(store_cls, http) -> None:
    """A response that is not SPARQL JSON raises ValueError, not KeyError."""
    http.response = _StubResponse(json_body={"unexpected": True}, text="{}")
    with pytest.raises(ValueError):
        store_cls("http://localhost:7878", GRAPH).query("SELECT ?s WHERE {}")


def test_construct_parses_turtle_into_a_graph(store_cls, http) -> None:
    """CONSTRUCT returns a populated rdflib Graph, requested as Turtle."""
    http.response = _StubResponse(text='<urn:a> <urn:p> "v" .')
    graph = store_cls("http://localhost:7878", GRAPH).construct("CONSTRUCT {} WHERE {}")

    assert isinstance(graph, Graph)
    assert len(graph) == 1
    assert http.calls[0][2]["headers"]["Accept"] == "text/turtle"


def test_construct_returns_an_empty_graph_for_an_empty_body(store_cls, http) -> None:
    """No triples is an empty Graph, not an error — read handlers rely on this."""
    http.response = _StubResponse(text="   \n")
    assert len(store_cls("http://localhost:7878", GRAPH).construct("CONSTRUCT {} WHERE {}")) == 0


def test_construct_rejects_invalid_turtle(store_cls, http) -> None:
    """An unparseable body raises ValueError rather than yielding a partial graph."""
    http.response = _StubResponse(text="this is not turtle {{{")
    with pytest.raises(ValueError):
        store_cls("http://localhost:7878", GRAPH).construct("CONSTRUCT {} WHERE {}")


def test_load_turtle_targets_the_configured_graph(store_cls, http) -> None:
    """Bulk load addresses ?graph=<uri> when a graph is configured."""
    store_cls("http://localhost:7878", GRAPH).load_turtle('<urn:a> <urn:p> "v" .')
    _, url, kwargs = http.calls[0]
    assert url == "http://localhost:7878/store"
    assert kwargs["params"] == {"graph": GRAPH}
    assert kwargs["content"] == b'<urn:a> <urn:p> "v" .'  # encoded, not str


def test_load_turtle_graph_argument_overrides_the_configured_graph(store_cls, http) -> None:
    """An explicit graph_name wins for that one call."""
    other = "https://rosfeatr.eu/rdf/vocab/"
    store_cls("http://localhost:7878", GRAPH).load_turtle("<urn:a> <urn:p> <urn:o> .", other)
    assert http.calls[0][2]["params"] == {"graph": other}


def test_load_turtle_without_a_graph_uses_default_explicitly(store_cls, http) -> None:
    """With no graph, ?default must be sent.

    Omitting both would make the Graph Store Protocol auto-create a fresh unnamed
    graph, which reads would never find.
    """
    store_cls("http://localhost:7878", None).load_turtle("<urn:a> <urn:p> <urn:o> .")
    assert http.calls[0][2]["params"] == {"default": ""}


def test_health_is_false_on_a_transport_error(store_cls, monkeypatch) -> None:
    """An unreachable store reports False rather than raising.

    The /health route and the startup readiness poll both depend on this.
    """

    class _Unreachable(_StubClient):
        def get(self, url, **kwargs):
            raise oxigraph_module.httpx.ConnectError("refused")

    monkeypatch.setattr(oxigraph_module.httpx, "Client", _Unreachable)
    assert store_cls("http://localhost:7878", GRAPH).health() is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, True), (404, True), (500, False), (503, False)],
)
def test_health_treats_any_sub_500_response_as_up(store_cls, http, status, expected) -> None:
    """Reachability, not correctness: a 404 still proves the store is answering."""
    http.response = _StubResponse(status_code=status)
    assert store_cls("http://localhost:7878", GRAPH).health() is expected


# ---------------------------------------------------------------------------
# The factory
# ---------------------------------------------------------------------------


class _Settings:
    """The structural slice build_triplestore reads (see StoreSettings)."""

    def __init__(self, triplestore: str):
        self.triplestore = triplestore
        self.oxigraph_url = "http://localhost:7878"
        self.data_graph_uri = GRAPH
        self.oxigraph_load_timeout = 42.0


@pytest.mark.parametrize("name", sorted(STORE_FACTORIES))
def test_factory_builds_each_registered_store(name) -> None:
    """Every registered name resolves, and settings are threaded through."""
    store = build_triplestore(_Settings(name))
    assert isinstance(store, STORE_FACTORIES[name])
    assert store.graph == GRAPH
    assert store.load_timeout == 42.0


@pytest.mark.parametrize("name", ["oxigraf", "", "OXIGRAPH_2"])
def test_factory_rejects_an_unknown_store(name) -> None:
    """A typo fails loudly at startup instead of silently defaulting."""
    with pytest.raises(ValueError, match="Unknown TRIPLESTORE"):
        build_triplestore(_Settings(name))


def test_factory_is_case_and_whitespace_insensitive() -> None:
    """Env vars pick up stray case and whitespace; that should not break a deploy."""
    assert isinstance(build_triplestore(_Settings("  Oxigraph \n")), OxigraphStore)


# ---------------------------------------------------------------------------
# Live round-trip — skipped unless a store is actually listening
# ---------------------------------------------------------------------------


def _reachable(host: str = "127.0.0.1", port: int = 7878) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _reachable(), reason="No triplestore reachable on localhost:7878")
def test_live_write_read_clear_round_trip() -> None:
    """load_turtle -> query -> clear_store against a real store.

    Uses a scratch graph so it cannot disturb the configured data graph, and the
    final clear_store call is why: it wipes everything.
    """
    scratch = "https://rosfeatr.eu/rdf/graph/contract-test/"
    store = OxigraphStore("http://127.0.0.1:7878", scratch)

    assert store.health() is True
    store.load_turtle('<urn:contract:a> <urn:contract:p> "value" .')

    select_o = f"SELECT ?o FROM <{scratch}> WHERE {{ <urn:contract:a> <urn:contract:p> ?o }}"
    assert [r["o"] for r in store.query(select_o)] == ["value"]

    graph = store.construct(
        f"CONSTRUCT {{ <urn:contract:a> ?p ?o }} FROM <{scratch}> "
        f"WHERE {{ <urn:contract:a> ?p ?o }}"
    )
    assert len(graph) == 1

    store.update(
        f"WITH <{scratch}> DELETE {{ <urn:contract:a> ?p ?o }} WHERE {{ <urn:contract:a> ?p ?o }}"
    )
    assert store.query(select_o) == []
