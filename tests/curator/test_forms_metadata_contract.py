"""Contract guard for the SHACL-extraction format exposed by /api/v1/curator/shapes and
/api/v1/curator/forms.

The frontend form generator depends on a stable set of keys in every shape and
field descriptor and on a fixed vocabulary of field ``type`` values. These tests
fail if the extractor accidentally adds, removes, or renames a metadata key, or
emits an unknown field type — the kind of drift that would silently break form
rendering without any obvious error.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.shapes import router
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


BASE = "https://rosfeatr.eu/rdf/schema/"
PLACE_SHAPE = BASE + "PlaceShape"

SHAPE_KEYS = {
    "id",
    "label",
    "description",
    "targetClass",
    "targetClassUri",
    "shapeRole",
    "additionalTypes",
    "typeOptions",
    "properties",
    "readOnly",
}

FIELD_KEYS = {
    "path",
    "pathUri",
    "name",
    "description",
    "type",
    "longText",
    "linkTemplate",
    "datatype",
    "datatypeOptions",
    "languageTagPolicy",
    "nodeKind",
    "nodeClass",
    "nestedShape",
    "nestedShapeRole",
    "minCount",
    "maxCount",
    "pattern",
    "in",
}

FIELD_TYPES = {
    "enum",
    "lang-string",
    "lang-string-list",
    "temporal",
    "year",
    "number",
    "text",
    "nested",
    "entity-search",
    "uri",
    "file-list",
}


def _client() -> TestClient:
    """Mount the shapes router over a real SchemaExtractor for the active schema."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/curator")
    app.state.schema_extractor = SchemaExtractor(str(SCHEMA_PATH))
    return TestClient(app)


def test_shapes_carry_exactly_the_expected_keys() -> None:
    """Every /api/v1/curator/shapes entry has the documented key set — no more, no less."""
    shapes = _client().get("/api/v1/curator/shapes").json()
    assert shapes
    for shape in shapes:
        assert set(shape) == SHAPE_KEYS, shape.get("id")


def test_forms_envelope_and_field_keys_are_stable() -> None:
    """/api/v1/curator/forms returns {shape, fields} and every field has the documented keys."""
    body = _client().get("/api/v1/curator/forms", params={"shapeId": PLACE_SHAPE}).json()
    assert set(body) == {"shape", "fields"}
    assert set(body["shape"]) == SHAPE_KEYS
    assert body["fields"]
    for field in body["fields"]:
        assert set(field) == FIELD_KEYS, field.get("path")


def test_every_field_type_is_known() -> None:
    """No shape exposes a field type outside the vocabulary the frontend handles."""
    for shape in _client().get("/api/v1/curator/shapes").json():
        for field in shape["properties"]:
            assert field["type"] in FIELD_TYPES, (shape["id"], field["path"], field["type"])


def test_core_shapes_are_present() -> None:
    """The primary record types remain discoverable (guards accidental removal)."""
    ids = {s["id"] for s in _client().get("/api/v1/curator/shapes").json()}
    for name in (
        "MusicalWorkShape",
        "ExpressionShape",
        "ManifestationShape",
        "SourceShape",
        "PersonShape",
        "AgentRoleShape",
        "LanguageShape",
    ):
        assert BASE + name in ids


def test_read_only_flag_follows_the_read_only_shapes_setting(monkeypatch) -> None:
    """Both routes stamp ``readOnly`` from this service's ``READ_ONLY_SHAPES``, not a literal.

    The stamp itself is covered in ``tests/core/test_shapes_stamp.py``; this checks
    the curator's wiring.
    """
    from core.config import settings

    monkeypatch.setattr(settings, "read_only_shapes", [PLACE_SHAPE])
    client = _client()

    flags = {s["id"]: s["readOnly"] for s in client.get("/api/v1/curator/shapes").json()}
    assert flags[PLACE_SHAPE] is True
    assert not any(flag for shape_id, flag in flags.items() if shape_id != PLACE_SHAPE)

    place = client.get("/api/v1/curator/forms", params={"shapeId": PLACE_SHAPE}).json()
    other = client.get("/api/v1/curator/forms", params={"shapeId": BASE + "SourceShape"}).json()
    assert place["shape"]["readOnly"] is True
    assert other["shape"]["readOnly"] is False
