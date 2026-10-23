"""What the read service must and must not be.

These are structural assertions rather than behavioural ones, and they exist
because the writer/reader split is the kind of boundary that erodes silently: a
convenient import, a route added to the wrong app, one more thing on
``app.state``. Each check below turns one of those into a test failure.

Covers: exactly three ``app.state`` entries, no write route, no validator,
``/shapes`` stamped with ``readOnly`` like the curator's, and ``/health`` without a
``seed`` key.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import RouteContext, iter_route_contexts

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "dataexplorer-backend"
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _import_app(monkeypatch: pytest.MonkeyPatch):
    """Import the read app with deterministic environment values."""
    monkeypatch.setenv("OXIGRAPH_URL", "http://localhost:7878")
    monkeypatch.setenv("DATA_GRAPH_URI", "https://rfdb.it/graph/data")
    monkeypatch.setenv("SCHEMA_PATH", str(SCHEMA_PATH))
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5174"]')
    monkeypatch.setenv("LOG_FILE", "logs/test-app.jsonl")
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    sys.modules.pop("app", None)
    sys.modules.pop("core.config", None)
    return importlib.import_module("app")


def _run_lifespan(app_module, fake_app) -> None:
    """Run a single lifespan cycle against a synthetic app object."""
    import asyncio

    async def _run() -> None:
        async with app_module.lifespan(fake_app):
            return None

    asyncio.run(_run())


def _effective_routes(app) -> list[RouteContext]:
    """Every registered route, flattened across ``include_router`` boundaries.

    From FastAPI 0.141, ``app.routes`` is not a flat list: ``include_router``
    appends a single lazy node per inclusion and resolves its children on demand,
    so a router's routes are not *in* the list. Reading ``.path`` off that node
    raises ``AttributeError``, and, worse, ``getattr(r, "methods", None)`` returns
    ``None``, which would let the read-only assertion below pass while inspecting
    nothing but ``/health`` and the docs routes.

    ``iter_route_contexts`` is FastAPI's own flattening helper — its OpenAPI
    generator uses it — and yields the effective, prefix-applied view. Preferred
    over ``app.openapi()["paths"]`` because that omits anything registered with
    ``include_in_schema=False``, and a route drifting in unnoticed is precisely
    what these tests exist to catch.
    """
    return list(iter_route_contexts(app.routes))


# ---------------------------------------------------------------------------
# app.state surface
# ---------------------------------------------------------------------------


def test_lifespan_sets_exactly_three_state_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """schema_extractor, store, storage — and nothing else.

    Between them the read routers need exactly these three. A fourth entry means
    either a write concern arrived or a router gained a dependency; either way it
    deserves a look rather than a silent pass.
    """
    app_module = _import_app(monkeypatch)
    monkeypatch.setattr(app_module, "build_triplestore", lambda _s: object())
    monkeypatch.setattr(app_module, "build_storage", lambda _s: object())

    fake_app = SimpleNamespace(state=SimpleNamespace())
    _run_lifespan(app_module, fake_app)

    assert set(vars(fake_app.state)) == {"schema_extractor", "store", "storage"}


def test_lifespan_never_seeds_or_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup touches no mutating store method, whatever the environment says.

    RESET_DATA_ON_STARTUP is set to true here deliberately: the reader has no
    such setting, so a stray value in a shared .env must be inert rather than
    destructive.
    """
    monkeypatch.setenv("RESET_DATA_ON_STARTUP", "true")
    monkeypatch.setenv("SEED_VOCAB_ON_STARTUP", "true")
    app_module = _import_app(monkeypatch)

    calls: list[str] = []

    class _RecordingStore:
        def __getattr__(self, name: str):
            calls.append(name)
            return lambda *a, **k: None

    monkeypatch.setattr(app_module, "build_triplestore", lambda _s: _RecordingStore())
    monkeypatch.setattr(app_module, "build_storage", lambda _s: object())

    _run_lifespan(app_module, SimpleNamespace(state=SimpleNamespace()))

    forbidden = {"clear_store", "load_turtle", "update"}
    assert not forbidden & set(calls), f"read service touched write methods: {calls}"


# ---------------------------------------------------------------------------
# Route surface
# ---------------------------------------------------------------------------


def test_every_route_is_a_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """No POST/PUT/PATCH/DELETE anywhere in the app."""
    app_module = _import_app(monkeypatch)
    verbs = {
        m
        for r in _effective_routes(app_module.app)
        for m in (r.methods or ())
        if m not in {"HEAD", "OPTIONS"}
    }
    assert verbs == {"GET"}, f"non-read verbs registered: {sorted(verbs - {'GET'})}"


def test_no_forms_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/v1/curator/forms drives an editing form, so it stays on the curator."""
    app_module = _import_app(monkeypatch)
    assert "/api/v1/curator/forms" not in {r.path for r in _effective_routes(app_module.app)}


def test_route_set_is_exactly_the_read_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the published surface so a route cannot drift in unnoticed.

    FastAPI's own /docs, /redoc and /openapi.json are excluded — they are
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
        # The data space: permanent public identifiers, unversioned.
        "/rdf/data/{local_name}",
        "/rdf/data/{file_id}/content",
        "/rdf/schema/{shape_name}",
        # This service's own operational surface: versioned, owner-named.
        "/api/v1/dataexplorer/shapes",
        "/api/v1/dataexplorer/entities",
        "/api/v1/dataexplorer/entities/counts",
        "/api/v1/dataexplorer/entities/get",
        "/api/v1/dataexplorer/entities/search",
        "/api/v1/dataexplorer/graph/node",
        "/api/v1/dataexplorer/meta/schema",
        "/api/v1/dataexplorer/meta/prefixes",
        "/api/v1/dataexplorer/meta/graphs",
        "/api/v1/dataexplorer/meta/files",
    }


def test_no_route_uses_a_greedy_path_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``{param:path}`` anywhere — it makes route resolution order-dependent.

    A greedy parameter swallows every sibling registered after it. When the siblings
    live in a *different router*, the ordering that keeps them reachable is expressed
    in ``app.py``, files away from either route, and breaking it yields a 400 from the
    IRI guard with no visible connection to router registration.

    So entity IRIs travel as ``?id=`` instead (matching ``/graph/node``), and the
    collision is impossible rather than merely guarded. This test is the structural
    version of that guarantee: it fails on *any* greedy route, not only one that
    shadows a known sibling.
    """
    app_module = _import_app(monkeypatch)
    greedy = [r.path for r in _effective_routes(app_module.app) if ":path}" in r.path]
    assert greedy == [], (
        f"greedy path parameters reintroduce order-dependent routing: {greedy}. "
        "Pass IRIs as ?id= instead."
    )


# ---------------------------------------------------------------------------
# /shapes is stamped, identically to the curator's
# ---------------------------------------------------------------------------


def test_shapes_carries_the_read_only_flag() -> None:
    """Every shape must carry ``readOnly`` — the reader serves the full catalogue.

    Not a write concern leaking into a read service: the flag states which shapes
    are *editable*, which is what a client needs to render a UI. Serving it here is
    what lets the editor start with the write tier stopped, rather than show an
    empty sidebar.
    """
    from api.shapes import list_shapes
    from rfdb_core.schema_extractor import SchemaExtractor

    extractor = SchemaExtractor(str(SCHEMA_PATH))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(schema_extractor=extractor))
    )

    shapes = list_shapes(request)
    assert shapes, "the schema defines shapes"
    assert all("readOnly" in s for s in shapes)
    assert all(isinstance(s["readOnly"], bool) for s in shapes)


def test_shapes_module_does_not_reimplement_the_stamp() -> None:
    """The flag must come from ``rfdb_core.shapes``, never from a local copy.

    Both services returning the same payload is an invariant maintained by *sharing
    the code*, not by two implementations that happen to agree today. A
    re-implementation here would drift and hand the editor flags that differ from
    the writer's, in a form no route test would catch.
    """
    source = (BACKEND_DIR / "api" / "shapes.py").read_text()
    assert "rfdb_core.shapes" in source, "must delegate to the shared implementation"
    assert "readOnly" not in source.split('"""')[-1], (
        "the reader builds the readOnly key itself instead of delegating"
    )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store_up", [True, False])
def test_health_reports_store_status_and_no_seed_key(
    monkeypatch: pytest.MonkeyPatch, store_up: bool
) -> None:
    """``{"status", "store"}`` exactly — the reader has no seed report to give."""
    app_module = _import_app(monkeypatch)
    app_module.app.state.store = SimpleNamespace(health=lambda: store_up)

    body = app_module.health()
    assert set(body) == {"status", "store"}
    assert body["status"] == "ok"
    assert body["store"] == ("up" if store_up else "down")
