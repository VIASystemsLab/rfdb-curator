"""Unit-level fail-closed coverage for backend write logic.

This module executes ``api.data.create_or_update_entity`` directly with a fake
request/app.state and a controlled Oxigraph stub. It does not test HTTP routing;
it tests backend branch semantics when related-entity merge fails.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "curator-backend"
DATA_NS = "https://rosfeatr.eu/rdf/data"
SHAPE_NS = "https://rosfeatr.eu/rdf/schema"


def _load_backend_symbols() -> SimpleNamespace:
    """Load service symbols directly from curator-backend/ for unit-level tests."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    from fastapi import HTTPException

    data_module = importlib.import_module("api.data")
    from api.data import create_or_update_entity
    from models.data import EntityData

    return SimpleNamespace(
        HTTPException=HTTPException,
        data_module=data_module,
        create_or_update_entity=create_or_update_entity,
        EntityData=EntityData,
    )


class _NoopValidator:
    """Validator stub that always reports conforming graphs."""

    def validate(self, _graph):
        return {"conforms": True, "violations": []}


class _RejectingValidator:
    """Validator stub that forces non-conformance to avoid write side effects."""

    def validate(self, _graph):
        return {
            "conforms": False,
            "violations": [
                {
                    "focusNode": f"{DATA_NS}/focus",
                    "path": "http://www.w3.org/2000/01/rdf-schema#label",
                    "message": "forced test violation",
                }
            ],
        }


class _FailingConstructOxigraph:
    """Oxigraph stub that fails on CONSTRUCT and records side-effect calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def from_clause(self) -> str:
        return ""

    def construct(self, _sparql: str):
        self.calls.append("construct")
        raise RuntimeError("simulated construct failure")

    def update(self, _sparql: str) -> None:
        self.calls.append("update")

    def load_turtle(self, _ttl: str) -> None:
        self.calls.append("load_turtle")


class _NoopOxigraph:
    """Oxigraph stub for seed-composition tests without write operations."""

    def from_clause(self) -> str:
        return ""

    def construct(self, _sparql: str):
        self.calls.append("construct")
        return None

    def with_clause(self) -> str:
        return ""

    def update(self, _sparql: str) -> None:
        self.calls.append("update")

    def load_turtle(self, _ttl: str) -> None:
        self.calls.append("load_turtle")

    def __init__(self) -> None:
        self.calls: list[str] = []


def _fake_request(oxigraph: _FailingConstructOxigraph) -> SimpleNamespace:
    """Build the minimal request.app.state object expected by the route logic."""

    state = SimpleNamespace(
        store=oxigraph,
        shape_dep_graph={},
        shacl_validator=_NoopValidator(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _fake_request_with_validator(*, store: object, validator: object) -> SimpleNamespace:
    """Build request.app.state with configurable validator and triplestore stubs."""

    state = SimpleNamespace(
        store=store,
        shape_dep_graph={},
        shacl_validator=validator,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_create_or_update_fails_closed_on_related_merge_error() -> None:
    """Related merge failures return 503 and do not execute write operations."""
    symbols = _load_backend_symbols()
    oxigraph = _FailingConstructOxigraph()
    request = _fake_request(oxigraph)

    payload = symbols.EntityData(
        shapeId=f"{SHAPE_NS}/ManifestationShape",
        data={
            "@context": {
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
            },
            "@id": f"{DATA_NS}/fail_closed_manifestation",
            "@type": "lrmoo:F3_Manifestation",
            "rdfs:label": {"@value": "Fail-Closed Test", "@language": "en"},
            "lrmoo:R4_embodies": {"@id": f"{DATA_NS}/fail_closed_expression"},
        },
        originalTriples=None,
    )

    # This directly executes backend production logic in api.data, bypassing
    # HTTP transport so we can deterministically force construct() failure.
    with pytest.raises(symbols.HTTPException) as exc:
        symbols.create_or_update_entity(payload, request)

    assert exc.value.status_code == 503
    assert "Related-entity merge failed" in str(exc.value.detail)
    assert oxigraph.calls == ["construct"]


@pytest.mark.parametrize(
    ("payload_data", "expected_seed_iris"),
    [
        (
            {
                "@id": f"{DATA_NS}/literals_only",
                "@type": f"{DATA_NS}/LocalType",
                "http://www.w3.org/2000/01/rdf-schema#label": {
                    "@value": "Only literals",
                    "@language": "en",
                },
            },
            {
                f"{DATA_NS}/literals_only",
                f"{DATA_NS}/LocalType",
            },
        ),
        (
            {
                "@id": f"{DATA_NS}/vocab_only",
                "@type": f"{DATA_NS}/LocalType",
                f"{DATA_NS}/pointsTo": {"@id": "http://www.w3.org/2000/01/rdf-schema#label"},
            },
            {
                f"{DATA_NS}/vocab_only",
                f"{DATA_NS}/LocalType",
            },
        ),
        (
            {
                "@id": f"{DATA_NS}/mixed_iris",
                "@type": f"{DATA_NS}/LocalType",
                f"{DATA_NS}/pointsTo": {"@id": f"{DATA_NS}/RelatedNode"},
                f"{DATA_NS}/vocabRef": {"@id": "http://www.w3.org/2001/XMLSchema#string"},
            },
            {
                f"{DATA_NS}/mixed_iris",
                f"{DATA_NS}/LocalType",
                f"{DATA_NS}/RelatedNode",
            },
        ),
        (
            {
                "@id": f"{DATA_NS}/repeated_non_vocab",
                "@type": f"{DATA_NS}/LocalType",
                f"{DATA_NS}/pointsTo": [
                    {"@id": f"{DATA_NS}/DedupNode"},
                    {"@id": f"{DATA_NS}/DedupNode"},
                ],
            },
            {
                f"{DATA_NS}/repeated_non_vocab",
                f"{DATA_NS}/LocalType",
                f"{DATA_NS}/DedupNode",
            },
        ),
    ],
)
def test_create_or_update_seed_iri_composition(
    monkeypatch: pytest.MonkeyPatch,
    payload_data: dict,
    expected_seed_iris: set[str],
) -> None:
    """Seed IRIs include non-vocab @type/object IRIs and deduplicate repeats."""
    symbols = _load_backend_symbols()
    captured: dict[str, object] = {}

    def _capture_build_validation_construct(
        dep_graph, root_shape_id: str, seed_iris: set[str], from_clause: str
    ) -> str:
        captured["dep_graph"] = dep_graph
        captured["root_shape_id"] = root_shape_id
        captured["seed_iris"] = set(seed_iris)
        captured["from_clause"] = from_clause
        return ""

    monkeypatch.setattr(
        symbols.data_module,
        "_build_validation_construct",
        _capture_build_validation_construct,
    )

    payload = symbols.EntityData(
        shapeId=f"{SHAPE_NS}/TestShape",
        data=payload_data,
        originalTriples=None,
    )

    oxigraph = _NoopOxigraph()
    request = _fake_request_with_validator(
        store=oxigraph,
        validator=_RejectingValidator(),
    )

    response = symbols.create_or_update_entity(payload, request)

    assert response.success is False
    assert set(captured["seed_iris"]) == expected_seed_iris
    assert captured["root_shape_id"] == f"{SHAPE_NS}/TestShape"


def test_create_or_update_rejects_in_read_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/curator/entities returns 403 when READ_ONLY mode is enabled."""
    symbols = _load_backend_symbols()
    oxigraph = _NoopOxigraph()
    request = _fake_request_with_validator(store=oxigraph, validator=_NoopValidator())

    monkeypatch.setattr(symbols.data_module.settings, "read_only", True)

    payload = symbols.EntityData(
        shapeId=f"{SHAPE_NS}/TestShape",
        data={"@context": {}, "@id": f"{DATA_NS}/read_only_test"},
        originalTriples=None,
    )

    with pytest.raises(symbols.HTTPException) as exc:
        symbols.create_or_update_entity(payload, request)

    assert exc.value.status_code == 403
    assert "READ_ONLY=true" in str(exc.value.detail)
    assert oxigraph.calls == []


def test_delete_rejects_in_read_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /api/v1/curator/entities?id=... returns 403 when READ_ONLY is enabled."""
    symbols = _load_backend_symbols()
    oxigraph = _NoopOxigraph()
    request = _fake_request_with_validator(store=oxigraph, validator=_NoopValidator())

    monkeypatch.setattr(symbols.data_module.settings, "read_only", True)

    with pytest.raises(symbols.HTTPException) as exc:
        symbols.data_module.delete_entity(request, entity_id=f"{DATA_NS}/read_only_test")

    assert exc.value.status_code == 403
    assert "READ_ONLY=true" in str(exc.value.detail)
    assert oxigraph.calls == []


def test_create_or_update_rejects_read_only_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/curator/entities returns 403 when the target shape is in READ_ONLY_SHAPES."""
    symbols = _load_backend_symbols()
    oxigraph = _NoopOxigraph()
    request = _fake_request_with_validator(store=oxigraph, validator=_NoopValidator())

    monkeypatch.setattr(
        symbols.data_module.settings,
        "read_only_shapes",
        [f"{SHAPE_NS}/LanguageShape"],
    )

    payload = symbols.EntityData(
        shapeId=f"{SHAPE_NS}/LanguageShape",
        data={"@id": f"{DATA_NS}/russ1263"},
        originalTriples=None,
    )

    with pytest.raises(symbols.HTTPException) as exc:
        symbols.create_or_update_entity(payload, request)

    assert exc.value.status_code == 403
    assert "read-only" in str(exc.value.detail).lower()
    assert oxigraph.calls == []


def test_create_or_update_allows_non_read_only_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/curator/entities is not blocked by the shape guard for non-read-only shapes."""
    symbols = _load_backend_symbols()
    oxigraph = _NoopOxigraph()
    request = _fake_request_with_validator(store=oxigraph, validator=_RejectingValidator())

    monkeypatch.setattr(
        symbols.data_module.settings,
        "read_only_shapes",
        [f"{SHAPE_NS}/LanguageShape"],
    )

    payload = symbols.EntityData(
        shapeId=f"{SHAPE_NS}/SourceShape",
        data={"@id": f"{DATA_NS}/TestSource"},
        originalTriples=None,
    )

    # Should not raise 403 — shape guard passes. May raise 503 due to stub
    # limitations (NoneType graph merge), which is fine: it proves execution
    # proceeded past the read-only check.
    with pytest.raises(symbols.HTTPException) as exc:
        symbols.create_or_update_entity(payload, request)

    assert exc.value.status_code != 403, "Shape guard must not block non-read-only shapes"


def test_delete_rejects_read_only_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /api/v1/curator/entities?id=…&shapeId=… returns 403 for a read-only shape."""
    symbols = _load_backend_symbols()
    oxigraph = _NoopOxigraph()
    request = _fake_request_with_validator(store=oxigraph, validator=_NoopValidator())

    monkeypatch.setattr(
        symbols.data_module.settings,
        "read_only_shapes",
        [f"{SHAPE_NS}/LanguageShape"],
    )

    with pytest.raises(symbols.HTTPException) as exc:
        symbols.data_module.delete_entity(
            request,
            entity_id=f"{DATA_NS}/russ1263",
            shapeId=f"{SHAPE_NS}/LanguageShape",
        )

    assert exc.value.status_code == 403
    assert "read-only" in str(exc.value.detail).lower()
    assert oxigraph.calls == []


def test_shapes_endpoint_stamps_read_only_flag() -> None:
    """The curator's shapes route flags shapes listed in READ_ONLY_SHAPES.

    The stamp itself is in ``rfdb_core.shapes``, shared with the reader so both
    services serve identical flags, and its pure-function behaviour is covered in
    ``tests/core/test_shapes_stamp.py``. What matters *here* is the curator-side
    wiring: that this service still applies the flag, and applies it from its own
    settings rather than from a hardcoded list.
    """
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    import importlib

    shapes_module = importlib.import_module("api.shapes")

    language_shape_uri = f"{SHAPE_NS}/LanguageShape"
    other_shape_uri = f"{SHAPE_NS}/SourceShape"

    locked = shapes_module.stamp_read_only(
        {"id": language_shape_uri, "label": "Language"}, [language_shape_uri]
    )
    unlocked = shapes_module.stamp_read_only(
        {"id": other_shape_uri, "label": "Source"}, [language_shape_uri]
    )

    assert locked["readOnly"] is True
    assert unlocked["readOnly"] is False

    # The route must read the deployment's own policy, not a literal.
    source = (BACKEND_DIR / "api" / "shapes.py").read_text()
    assert "settings.read_only_shapes" in source
