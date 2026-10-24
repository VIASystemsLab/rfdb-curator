"""External identifiers stored as text: the URL rule, the setting, and the field template."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rfdb_core.config import BaseServiceSettings
from rfdb_core.external_ids import external_id_url
from rfdb_core.schema_extractor import SchemaExtractor

SCHEMA_PATH = str(Path(__file__).resolve().parents[2] / "schema" / "rfdb_schema.ttl")
VIAF = "http://www.wikidata.org/prop/direct/P214"
RISM = "http://www.wikidata.org/prop/direct/P5504"


def test_identifier_fills_the_template_with_slashes_kept() -> None:
    """A path-like identifier keeps its ``/``; anything unsafe is percent-encoded."""
    assert external_id_url("https://rism.online/$1", "sources/990052516") == (
        "https://rism.online/sources/990052516"
    )
    assert external_id_url("https://viaf.org/en/viaf/$1", " 293925180 ") == (
        "https://viaf.org/en/viaf/293925180"
    )
    assert external_id_url("https://x.org/$1", "a b?c") == "https://x.org/a%20b%3Fc"


def test_default_setting_links_viaf_and_rism() -> None:
    """Without the variable, the rfdb schema's two identifiers are linked."""
    links = BaseServiceSettings().external_id_links
    assert links[VIAF] == "https://viaf.org/en/viaf/$1"
    assert links[RISM] == "https://rism.online/$1"


def test_setting_parses_json_and_an_empty_value() -> None:
    """The variable is a JSON object string; an empty one means no links."""
    parsed = BaseServiceSettings(external_id_links=f'{{"{VIAF}": "https://v.org/$1"}}')
    assert parsed.external_id_links == {VIAF: "https://v.org/$1"}
    assert BaseServiceSettings(external_id_links="").external_id_links == {}


def test_template_without_placeholder_is_refused() -> None:
    """A template with no ``$1`` would drop the identifier, so startup fails."""
    with pytest.raises(ValidationError, match=r"no \$1"):
        BaseServiceSettings(external_id_links={VIAF: "https://viaf.org/"})


def test_matching_fields_carry_their_template() -> None:
    """Every field whose path has a template carries it; no other field does."""
    extractor = SchemaExtractor(SCHEMA_PATH, external_id_links={VIAF: "https://v.org/$1"})
    fields = [p for s in extractor.get_all_shapes() for p in s["properties"]]
    assert any(f["pathUri"] == VIAF for f in fields), "schema has no VIAF field"
    for field in fields:
        expected = "https://v.org/$1" if field["pathUri"] == VIAF else None
        assert field["linkTemplate"] == expected
