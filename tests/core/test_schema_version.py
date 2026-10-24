"""The schema states its own release with ``owl:versionInfo``; the extractor reads it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rfdb_core.schema_extractor import SchemaExtractor

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "rfdb_schema.ttl"

_HEADER = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex: <http://example.org/> .
"""


def _extractor(tmp_path: Path, body: str) -> SchemaExtractor:
    schema = tmp_path / "schema.ttl"
    schema.write_text(_HEADER + body, encoding="utf-8")
    return SchemaExtractor(str(schema))


def test_vendored_schema_states_a_semver_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", SchemaExtractor(str(SCHEMA_PATH)).version or "")


def test_reads_the_version(tmp_path: Path) -> None:
    assert _extractor(tmp_path, 'ex:onto a owl:Ontology ; owl:versionInfo "1.4.0" .').version == (
        "1.4.0"
    )


def test_no_version_is_none(tmp_path: Path) -> None:
    assert _extractor(tmp_path, "ex:onto a owl:Ontology .").version is None


def test_two_versions_fail_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="several versions"):
        _extractor(
            tmp_path,
            'ex:a a owl:Ontology ; owl:versionInfo "1.0.0" .\n'
            'ex:b a owl:Ontology ; owl:versionInfo "2.0.0" .',
        )
