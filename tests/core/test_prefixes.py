"""The CURIE prefix map is read from the schema's ``sh:declare`` declarations.

``declared_prefixes`` replaced a hand-maintained dict, so these pin what the dict
used to guarantee by construction: one namespace per prefix, nothing from
rdflib's pre-bound vocabularies, and every prefix the shipped TTL files use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from rfdb_core.prefixes import declared_prefixes, scan_ttl_prefixes
from rfdb_core.schema_extractor import SchemaExtractor

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "rfdb_schema.ttl"

_HEADER = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <http://example.org/> .
"""


def _graph(body: str) -> Graph:
    return Graph().parse(data=_HEADER + body, format="turtle")


def test_reads_declarations() -> None:
    graph = _graph("""
        ex:onto sh:declare
            [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ] ,
            [ sh:prefix "foo" ; sh:namespace "http://foo.example/"^^xsd:anyURI ] .
    """)
    assert declared_prefixes(graph) == {
        "ex": "http://example.org/",
        "foo": "http://foo.example/",
    }


def test_no_declarations_gives_empty_map() -> None:
    """A schema without sh:declare gets an empty map, never rdflib's pre-bound ones."""
    assert declared_prefixes(_graph("ex:a ex:b ex:c .")) == {}


def test_same_prefix_same_namespace_twice_is_fine() -> None:
    graph = _graph("""
        ex:one sh:declare [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ] .
        ex:two sh:declare [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ] .
    """)
    assert declared_prefixes(graph) == {"ex": "http://example.org/"}


def test_conflicting_namespaces_raise() -> None:
    graph = _graph("""
        ex:one sh:declare [ sh:prefix "ex" ; sh:namespace "http://example.org/"^^xsd:anyURI ] .
        ex:two sh:declare [ sh:prefix "ex" ; sh:namespace "http://other.example/"^^xsd:anyURI ] .
    """)
    with pytest.raises(ValueError, match="'ex'"):
        declared_prefixes(graph)


def test_incomplete_declaration_is_skipped() -> None:
    graph = _graph('ex:onto sh:declare [ sh:prefix "ex" ] .')
    assert declared_prefixes(graph) == {}


def test_extractor_exposes_the_schema_map() -> None:
    prefixes = SchemaExtractor(str(SCHEMA_PATH)).prefixes
    assert prefixes["rfdb"] == "https://rosfeatr.eu/rdf/data/"
    assert prefixes["wd"] == "http://www.wikidata.org/entity/"


def test_schema_declares_every_prefix_the_ttl_files_use() -> None:
    """The same rule as scripts/check_prefixes.py, so a test run catches drift too."""
    ttl_files = [str(p) for p in [*REPO_ROOT.glob("schema/*.ttl"), *REPO_ROOT.glob("data/*.ttl")]]
    used = scan_ttl_prefixes(ttl_files)
    declared = SchemaExtractor(str(SCHEMA_PATH)).prefixes
    assert {p: ns for p, ns in used.items() if declared.get(p) != ns} == {}
