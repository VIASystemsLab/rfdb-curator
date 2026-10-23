"""The writer's route surface, pinned as a specification (decision D8).

The reader has the same guard in ``tests/dataexplorer/test_app_contract.py``. Two
route-set assertions, one per service, are what make the URL partition enforceable
rather than aspirational: a route added to the wrong service, or a namespace that
drifts back toward the old unowned ``/api/…``, fails here instead of surfacing as a
405 in someone's browser.

Why this matters more than it looks: every symptom that produced this refactor's
corrections (C17–C20) was a URL that identified an operation on a host rather than a
resource. The partition is the fix, so it needs a test.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.routing import RouteContext, iter_route_contexts

BACKEND_DIR = Path(__file__).resolve().parents[2] / "curator-backend"

API_PREFIX = "/api/v1/curator"


def _import_app(monkeypatch: pytest.MonkeyPatch):
    """Import curator-backend's ``app`` module with a deterministic environment."""
    monkeypatch.setenv("OXIGRAPH_URL", "http://localhost:7878")
    monkeypatch.setenv("DATA_GRAPH_URI", "https://rfdb.it/graph/data")
    monkeypatch.setenv("SCHEMA_PATH", "schema/rfdb_schema.ttl")
    monkeypatch.setenv("VOCAB_PATH", '["data/rfdb_vocab.ttl"]')
    monkeypatch.setenv("DATA_PATH", "data/rfdb_data.ttl")
    monkeypatch.setenv("RESET_DATA_ON_STARTUP", "false")
    monkeypatch.setenv("SEED_VOCAB_ON_STARTUP", "false")
    monkeypatch.setenv("SEED_TEST_DATA_ON_STARTUP", "false")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')

    monkeypatch.chdir(BACKEND_DIR)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    for name in [m for m in sys.modules if m == "app" or m.startswith(("api.", "core."))]:
        del sys.modules[name]
    return importlib.import_module("app")


def _effective_routes(app) -> list[RouteContext]:
    """Every registered route, flattened across ``include_router`` boundaries.

    Necessary since FastAPI 0.141, which made ``app.routes`` hold one lazy node
    per ``include_router`` call rather than the included routes themselves. The
    full reasoning, and why this is preferred over reading the OpenAPI paths, is
    in the twin of this helper in ``tests/dataexplorer/test_app_contract.py``.
    """
    return list(iter_route_contexts(app.routes))


def test_route_set_is_exactly_the_writer_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the writer's surface so a route cannot drift in unnoticed.

    FastAPI's ``/docs``, ``/redoc`` and ``/openapi.json`` are excluded — they are
    generated from whatever is registered, not part of the API contract.
    """
    app_module = _import_app(monkeypatch)
    paths = {
        r.path
        for r in _effective_routes(app_module.app)
        if r.methods and r.path.startswith(("/api", "/rdf", "/health"))
    }
    assert paths == {
        "/health",
        f"{API_PREFIX}/shapes",
        f"{API_PREFIX}/forms",
        f"{API_PREFIX}/entities",
        f"{API_PREFIX}/validate",
        f"{API_PREFIX}/files/staged",
        f"{API_PREFIX}/files/staged/{{file_id}}",
    }


def test_writer_publishes_nothing_in_the_data_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``/rdf/`` route here — that space belongs to the reader.

    Permanent identifiers must not resolve to the tier that happens to mint them:
    the writer is the one component a read-only deployment omits entirely, so an
    IRI answered by it would stop dereferencing the moment that deployment shipped.
    """
    app_module = _import_app(monkeypatch)
    rdf_routes = [r.path for r in _effective_routes(app_module.app) if r.path.startswith("/rdf")]
    assert rdf_routes == []


def test_every_route_is_namespaced_by_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing may sit at the old unowned ``/api/…`` root.

    That root is what let one path mean two things depending on which service
    answered — ``GET`` and ``DELETE /api/data/{id}`` on different services (which no
    prefix-keyed proxy could route), and one ``/api/shapes`` with two payloads
    (C20).
    """
    app_module = _import_app(monkeypatch)
    stray = [
        r.path
        for r in _effective_routes(app_module.app)
        if r.path.startswith("/api") and not r.path.startswith(API_PREFIX)
    ]
    assert stray == [], f"routes outside the curator namespace: {stray}"


def test_no_route_uses_a_greedy_path_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``{param:path}`` here either — entity IRIs travel as ``?id=``.

    ``POST`` and ``DELETE`` share the ``/entities`` path and are told apart by method,
    so there is no sibling to shadow on this service. The rule is kept anyway: what
    actually trips callers is *how the IRI is passed*, and a path-encoded IRI needs
    double-encoding to survive while a bare local name fails the unsafe-IRI guard.
    One convention across both services means one thing to document.
    """
    app_module = _import_app(monkeypatch)
    greedy = [r.path for r in _effective_routes(app_module.app) if ":path}" in r.path]
    assert greedy == [], f"pass IRIs as ?id= rather than as a path segment: {greedy}"


def test_no_read_routes_leaked_back_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reads that moved to dataexplorer must not reappear here.

    ``forms`` and ``shapes`` are the deliberate exceptions and are asserted by the
    route-set test above: forms because it drives an editing form, shapes because
    both services serve the identical catalogue (D11).
    """
    app_module = _import_app(monkeypatch)
    paths = {r.path for r in _effective_routes(app_module.app)}
    for moved in ("entities/search", "graph/node", "meta/prefixes", "meta/graphs", "meta/files"):
        assert not any(moved in p for p in paths), f"{moved} is a read — it belongs to the reader"
