"""Long-text fields come from the LONG_TEXT_PREDICATES setting, not from code."""

from __future__ import annotations

from pathlib import Path

from rfdb_core.config import BaseServiceSettings
from rfdb_core.schema_extractor import SchemaExtractor

SCHEMA_PATH = str(Path(__file__).resolve().parents[2] / "schema" / "schema.ttl")
CORE_TEXT = "https://w3id.org/polifonia/ontology/core/text"


def _fields(extractor: SchemaExtractor) -> list[dict]:
    return [prop for shape in extractor.get_all_shapes() for prop in shape["properties"]]


def test_listed_predicates_are_long_text_and_no_others() -> None:
    """A field is long text exactly when its path is in the list."""
    fields = _fields(SchemaExtractor(SCHEMA_PATH, [CORE_TEXT]))
    assert any(f["pathUri"] == CORE_TEXT for f in fields), "schema has no core:text field"
    for field in fields:
        assert field["longText"] is (field["pathUri"] == CORE_TEXT)


def test_without_a_list_no_field_is_long_text() -> None:
    """An extractor given no list renders every text field on one line."""
    assert not any(f["longText"] for f in _fields(SchemaExtractor(SCHEMA_PATH)))


def test_setting_parses_a_json_array_and_an_empty_value() -> None:
    """The variable is a JSON array string; an empty one means no long-text fields."""
    assert BaseServiceSettings(long_text_predicates=f'["{CORE_TEXT}"]').long_text_predicates == [
        CORE_TEXT
    ]
    assert BaseServiceSettings(long_text_predicates="").long_text_predicates == []
