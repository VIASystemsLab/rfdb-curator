"""Startup and lifecycle coverage for the backend FastAPI app.

These tests validate the lifespan initialization order, ensure the first write
path can read app.state after startup, and confirm schema failures stop startup
before a partially initialized app can serve write routes.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rdflib import Graph
from rdflib.plugins.parsers.notation3 import BadSyntax

BACKEND_DIR = Path(__file__).resolve().parents[2] / "curator-backend"


def _import_backend_app(monkeypatch: pytest.MonkeyPatch, reset_data_on_startup: bool = False):
    """Import the backend app with deterministic environment values."""
    monkeypatch.setenv("OXIGRAPH_URL", "http://localhost:7878")
    monkeypatch.setenv("DATA_GRAPH_URI", "https://rfdb.it/graph/data")
    monkeypatch.setenv("SCHEMA_PATH", "schema/schema.ttl")
    monkeypatch.setenv("VOCAB_PATH", '["data/vocab.ttl"]')
    monkeypatch.setenv("DATA_PATH", "data/data.ttl")
    monkeypatch.setenv("RESET_DATA_ON_STARTUP", str(reset_data_on_startup).lower())
    monkeypatch.setenv("SEED_VOCAB_ON_STARTUP", "false")
    monkeypatch.setenv("SEED_TEST_DATA_ON_STARTUP", "false")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("LOG_FILE", "logs/test-app.jsonl")
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    sys.modules.pop("app", None)
    sys.modules.pop("core.config", None)

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    return importlib.import_module("app")


def _seeder_module():
    """The module `bootstrap_store` resolves `seed_store` from.

    The lifespan calls `core.seeder.bootstrap_store()`, which looks `seed_store`
    up in its own module globals — so a stub has to be installed there, not in
    app.py's namespace. Patching app.py would silently miss (and, since the name
    is no longer imported there, monkeypatch would raise instead).
    """
    return importlib.import_module("core.seeder")


def _run_lifespan(app_module, fake_app) -> None:
    """Run a single lifespan cycle for a synthetic FastAPI app object."""

    async def _run() -> None:
        async with app_module.lifespan(fake_app):
            return None

    asyncio.run(_run())


def _make_fake_app() -> SimpleNamespace:
    """Build the minimal app object shape expected by the lifespan."""

    return SimpleNamespace(state=SimpleNamespace())


def test_startup_initialization_order_populates_services_before_seeding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema services initialize before Oxigraph and seeding during startup."""
    app_module = _import_backend_app(monkeypatch)
    calls: list[str] = []

    class SchemaExtractorStub:
        version = None

        def __init__(self, schema_path: str):
            calls.append("schema_extractor")
            self.schema_path = schema_path

    class ShaclValidatorStub:
        def __init__(self, schema_path: str):
            assert calls == ["schema_extractor", "shape_dep_graph"]
            calls.append("validator")
            self.schema_path = schema_path

        def validate(self, _graph):
            return {"conforms": True, "violations": []}

    class StoreStub:
        def __init__(self, base_url: str, data_graph_uri: str, load_timeout: float = 300.0):
            assert calls == ["schema_extractor", "shape_dep_graph", "validator"]
            calls.append("store")
            self.base_url = base_url
            self.data_graph_uri = data_graph_uri

        def health(self) -> bool:
            return True

        def clear_store(self) -> None:
            calls.append("clear_store")

        def load_turtle(self, _ttl: str) -> None:
            calls.append("load_turtle")

        def from_clause(self) -> str:
            return ""

    def build_shape_dep_graph_stub(extractor):
        assert isinstance(extractor, SchemaExtractorStub)
        calls.append("shape_dep_graph")
        return {"urn:shape:root": SimpleNamespace(target_class_uri=None, edges=[])}

    def seed_store_stub(**_kwargs):
        assert calls == ["schema_extractor", "shape_dep_graph", "validator", "store"]
        calls.append("seed_store")
        return {"seedVocab": False, "seedTestData": False, "results": []}

    monkeypatch.setattr(app_module, "SchemaExtractor", SchemaExtractorStub)
    monkeypatch.setattr(app_module, "ShaclValidator", ShaclValidatorStub)

    def build_triplestore_stub(settings):
        return StoreStub(
            str(settings.oxigraph_url),
            settings.data_graph_uri,
            load_timeout=settings.oxigraph_load_timeout,
        )

    monkeypatch.setattr(app_module, "build_triplestore", build_triplestore_stub)
    monkeypatch.setattr(app_module, "_build_shape_dep_graph", build_shape_dep_graph_stub)
    monkeypatch.setattr(_seeder_module(), "seed_store", seed_store_stub)

    fake_app = _make_fake_app()
    _run_lifespan(app_module, fake_app)

    assert calls == [
        "schema_extractor",
        "shape_dep_graph",
        "validator",
        "store",
        "seed_store",
    ]
    assert fake_app.state.schema_extractor.schema_path == "schema/schema.ttl"
    assert fake_app.state.shacl_validator.schema_path == "schema/schema.ttl"
    assert fake_app.state.store.base_url == "http://localhost:7878/"
    assert fake_app.state.seed_report == {
        "seedVocab": False,
        "seedTestData": False,
        "results": [],
    }


def test_reset_data_on_startup_clears_store_before_seeding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset-on-startup clears Oxigraph before the store is seeded."""
    app_module = _import_backend_app(monkeypatch, reset_data_on_startup=True)
    calls: list[str] = []

    class SchemaExtractorStub:
        version = None

        def __init__(self, schema_path: str):
            calls.append("schema_extractor")
            self.schema_path = schema_path

    class ShaclValidatorStub:
        def __init__(self, schema_path: str):
            calls.append("validator")
            self.schema_path = schema_path

        def validate(self, _graph):
            return {"conforms": True, "violations": []}

    class StoreStub:
        def __init__(self, base_url: str, data_graph_uri: str, load_timeout: float = 300.0):
            calls.append("store")
            self.base_url = base_url
            self.data_graph_uri = data_graph_uri

        def health(self) -> bool:
            return True

        def clear_store(self) -> None:
            calls.append("clear_store")

        def load_turtle(self, _ttl: str) -> None:
            calls.append("load_turtle")

    def build_shape_dep_graph_stub(extractor):
        assert isinstance(extractor, SchemaExtractorStub)
        calls.append("shape_dep_graph")
        return {"urn:shape:root": SimpleNamespace(target_class_uri=None, edges=[])}

    def seed_store_stub(**_kwargs):
        assert calls == [
            "schema_extractor",
            "shape_dep_graph",
            "validator",
            "store",
            "clear_store",
        ]
        calls.append("seed_store")
        return {"seedVocab": False, "seedTestData": False, "results": []}

    monkeypatch.setattr(app_module, "SchemaExtractor", SchemaExtractorStub)
    monkeypatch.setattr(app_module, "ShaclValidator", ShaclValidatorStub)

    def build_triplestore_stub(settings):
        return StoreStub(
            str(settings.oxigraph_url),
            settings.data_graph_uri,
            load_timeout=settings.oxigraph_load_timeout,
        )

    monkeypatch.setattr(app_module, "build_triplestore", build_triplestore_stub)
    monkeypatch.setattr(app_module, "_build_shape_dep_graph", build_shape_dep_graph_stub)
    monkeypatch.setattr(_seeder_module(), "seed_store", seed_store_stub)

    fake_app = _make_fake_app()
    _run_lifespan(app_module, fake_app)

    assert calls == [
        "schema_extractor",
        "shape_dep_graph",
        "validator",
        "store",
        "clear_store",
        "seed_store",
    ]


def test_first_write_can_read_shape_dep_graph_after_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first create call can access app.state.shape_dep_graph immediately."""
    app_module = _import_backend_app(monkeypatch)
    captured: dict[str, object] = {}

    class SchemaExtractorStub:
        version = None

        def __init__(self, schema_path: str):
            self.schema_path = schema_path

    class ShaclValidatorStub:
        def __init__(self, schema_path: str):
            self.schema_path = schema_path

        def validate(self, _graph):
            return {"conforms": False, "violations": []}

    class StoreStub:
        def __init__(self, base_url: str, data_graph_uri: str, load_timeout: float = 300.0):
            self.base_url = base_url
            self.data_graph_uri = data_graph_uri

        def health(self) -> bool:
            return True

        def from_clause(self) -> str:
            return ""

        def construct(self, _sparql: str):
            return Graph()

        def update(self, _sparql: str) -> None:
            raise AssertionError("update should not run in this readiness test")

        def load_turtle(self, _ttl: str) -> None:
            raise AssertionError("load_turtle should not run in this readiness test")

    def build_shape_dep_graph_stub(extractor):
        return {"urn:shape:manifestation": SimpleNamespace(target_class_uri=None, edges=[])}

    def seed_store_stub(**_kwargs):
        return {"seedVocab": False, "seedTestData": False, "results": []}

    monkeypatch.setattr(app_module, "SchemaExtractor", SchemaExtractorStub)
    monkeypatch.setattr(app_module, "ShaclValidator", ShaclValidatorStub)

    def build_triplestore_stub(settings):
        return StoreStub(
            str(settings.oxigraph_url),
            settings.data_graph_uri,
            load_timeout=settings.oxigraph_load_timeout,
        )

    monkeypatch.setattr(app_module, "build_triplestore", build_triplestore_stub)
    monkeypatch.setattr(app_module, "_build_shape_dep_graph", build_shape_dep_graph_stub)
    monkeypatch.setattr(_seeder_module(), "seed_store", seed_store_stub)

    fake_app = _make_fake_app()
    _run_lifespan(app_module, fake_app)

    data_module = importlib.import_module("api.data")
    entity_models = importlib.import_module("models.data")

    def capture_build_validation_construct(
        dep_graph, root_shape_id: str, seed_iris: set[str], from_clause: str
    ) -> str:
        captured["dep_graph"] = dep_graph
        captured["root_shape_id"] = root_shape_id
        captured["seed_iris"] = set(seed_iris)
        captured["from_clause"] = from_clause
        return ""

    monkeypatch.setattr(
        data_module,
        "_build_validation_construct",
        capture_build_validation_construct,
    )

    payload = entity_models.EntityData(
        shapeId="urn:shape:manifestation",
        data={
            "@id": "https://rosfeatr.eu/rdf/data/startup_manifestation",
            "@type": "https://rosfeatr.eu/rdf/data/Manifestation",
            "https://rosfeatr.eu/rdf/data/embodies": {
                "@id": "https://rosfeatr.eu/rdf/data/startup_expression"
            },
        },
        originalTriples=None,
    )

    request = SimpleNamespace(app=fake_app)
    response = data_module.create_or_update_entity(payload, request)

    assert response.success is False
    assert captured["dep_graph"] is fake_app.state.shape_dep_graph
    assert captured["root_shape_id"] == "urn:shape:manifestation"
    assert "https://rosfeatr.eu/rdf/data/startup_manifestation" in captured["seed_iris"]
    assert "https://rosfeatr.eu/rdf/data/startup_expression" in captured["seed_iris"]


def test_startup_failure_stops_before_partially_initialized_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema syntax failures abort startup before write routes can be served."""
    app_module = _import_backend_app(monkeypatch)

    class FailingSchemaExtractor:
        def __init__(self, _schema_path: str):
            raise BadSyntax("schema/schema.ttl", 0, "", 0, "broken schema")

    monkeypatch.setattr(app_module, "SchemaExtractor", FailingSchemaExtractor)

    fake_app = _make_fake_app()

    with pytest.raises(BadSyntax):
        _run_lifespan(app_module, fake_app)

    assert not hasattr(fake_app.state, "schema_extractor")
    assert not hasattr(fake_app.state, "shape_dep_graph")
    assert not hasattr(fake_app.state, "shacl_validator")
    assert not hasattr(fake_app.state, "store")
    assert not hasattr(fake_app.state, "seed_report")
