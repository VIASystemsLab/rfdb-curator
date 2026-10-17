"""The published-resource test for digital copies: ``is_file_referenced``.

Covered here rather than only through the reader's download route because this
predicate is what decides whether a file is public at all, and two of its
properties are invisible from that route: the malformed-id guard (the route
validates before calling, so the guard is unreachable from there) and the
schema-driven choice of link predicates.

The link predicates come from the schema, never a hardcoded list, so these tests
use the real ``schema.ttl`` — a schema change that stopped linking digital copies
should fail here rather than silently make every file unpublished.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from rfdb_core.files_state import (
    file_content_url,
    is_file_referenced,
    staged_content_url,
)
from rfdb_core.schema_extractor import SchemaExtractor
from rfdb_core.vocab import RFDB_BASE, SCHEMA_DIGITAL_DOCUMENT

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "schema.ttl"

_EXTRACTOR = SchemaExtractor(str(SCHEMA_PATH))
FILE_ID = "File_0123abcd"


class _FakeStore:
    """Store stub over a real rdflib graph, so the SPARQL actually runs."""

    def __init__(self, turtle: str = "") -> None:
        self.g = Graph()
        if turtle:
            self.g.parse(data=turtle, format="turtle")

    def from_clause(self) -> str:
        return ""

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        return [{str(k): str(v) for k, v in b.items()} for b in self.g.query(sparql).bindings]


def _link_predicate() -> str:
    """A predicate the schema actually declares as linking a parent to a copy."""
    links = _EXTRACTOR.file_links()
    assert links, "schema.ttl declares no file-upload link"
    return links[0][1]


def test_referenced_when_a_parent_links_to_it() -> None:
    """A parent entity pointing at the file via a schema predicate makes it public."""
    store = _FakeStore(f"<{RFDB_BASE}S1> <{_link_predicate()}> <{RFDB_BASE}{FILE_ID}> .")
    assert is_file_referenced(FILE_ID, store, _EXTRACTOR) is True


def test_not_referenced_when_only_typed() -> None:
    """A typed-but-unlinked node is an orphan, not a published resource.

    Same notion of "linked" the reconciler uses, which is what keeps the download
    route and ``cleanup_files.py`` from disagreeing about what is referenced.
    """
    store = _FakeStore(f"<{RFDB_BASE}{FILE_ID}> a <{SCHEMA_DIGITAL_DOCUMENT}> .")
    assert is_file_referenced(FILE_ID, store, _EXTRACTOR) is False


def test_not_referenced_in_an_empty_store() -> None:
    """Nothing staged and nothing written: not published."""
    assert is_file_referenced(FILE_ID, _FakeStore(), _EXTRACTOR) is False


def test_an_unrelated_predicate_does_not_publish_a_file() -> None:
    """Only schema-declared link predicates count, not any inbound edge.

    Otherwise an incidental triple — provenance, annotation, anything pointing at
    the node — would be enough to publish bytes the schema never linked.
    """
    store = _FakeStore(f"<{RFDB_BASE}S1> <http://example.org/mentions> <{RFDB_BASE}{FILE_ID}> .")
    assert is_file_referenced(FILE_ID, store, _EXTRACTOR) is False


@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-file-id",
        "File_XYZ",
        "File_0123abc",  # 7 hex, one short
        "../etc/passwd",
        "",
        # Would close the IRI and append a second pattern if interpolated raw.
        "File_0123abcd> . ?s ?p ?o . <x",
    ],
)
def test_malformed_ids_are_refused_before_reaching_the_store(bad_id: str) -> None:
    """The id is interpolated into SPARQL, so a bad one must never get that far.

    The store stub raises if queried, proving the guard fires first rather than
    relying on the caller having validated.
    """

    class _Explode:
        def from_clause(self) -> str:
            return ""

        def query(self, _sparql: str):
            raise AssertionError("store was queried with a malformed file id")

    with pytest.raises(ValueError):
        is_file_referenced(bad_id, _Explode(), _EXTRACTOR)


def test_staged_and_published_urls_are_different_paths() -> None:
    """The two lifecycle states must not collapse onto one URL.

    They are served by different services: the staged path by the writer that
    accepted the upload, the published path by the reader.
    """
    assert staged_content_url(FILE_ID) != file_content_url(FILE_ID)
    assert staged_content_url(FILE_ID) == f"/api/v1/curator/files/staged/{FILE_ID}"
    assert file_content_url(FILE_ID) == f"{RFDB_BASE}{FILE_ID}/content"


def test_published_content_url_is_an_absolute_cool_uri() -> None:
    """The persisted value must be a dereferenceable IRI, not an application path.

    This is decision D9, and it is the one property a URL redesign must not break
    again: ``schema:contentUrl`` goes into the store, so anything relative couples
    persisted data to the API layout — which is what made this task a data
    migration. Asserted structurally (built from ``RFDB_BASE``, under the resource's
    own IRI) rather than against a literal, so a namespace change stays a one-line
    edit in ``vocab.py``.
    """
    url = file_content_url(FILE_ID)
    assert url.startswith("https://")
    assert url.startswith(RFDB_BASE)
    # A representation *of* the resource, so it must live under the entity's IRI.
    assert url == f"{RFDB_BASE}{FILE_ID}/content"

    # The staged path is deliberately still relative: it is one service's working
    # state, not a published identity. The client picks a base on that distinction.
    assert not staged_content_url(FILE_ID).startswith("http")


_RENAMED_UPLOAD_SCHEMA = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix schema: <http://schema.org/> .
@prefix ex: <http://example.org/> .

ex:PosterScanShape a sh:NodeShape ;
  sh:targetClass schema:DigitalDocument ;
  sh:property [ sh:path schema:name ; sh:datatype xsd:string ] .

ex:FilmShape a sh:NodeShape ;
  sh:targetClass ex:Film ;
  sh:property [ sh:path rdfs:label ; sh:datatype xsd:string ] ;
  sh:property [ sh:path ex:poster ; sh:node ex:PosterScanShape ] .

ex:ScreeningShape a sh:NodeShape ;
  sh:targetClass ex:Screening ;
  sh:property [ sh:path rdfs:label ; sh:datatype xsd:string ] ;
  sh:property [ sh:path ex:flyer ; sh:node ex:PosterScanShape ] ;
  sh:property [ sh:path ex:film ; sh:node ex:FilmShape ] .
"""


def _extractor(tmp_path: Path, turtle: str) -> SchemaExtractor:
    schema = tmp_path / "schema.ttl"
    schema.write_text(turtle, encoding="utf-8")
    return SchemaExtractor(str(schema))


def test_file_links_follow_the_upload_rule_not_a_shape_name(tmp_path: Path) -> None:
    """Any name works, and every class linking to the upload shape is a link."""
    links = _extractor(tmp_path, _RENAMED_UPLOAD_SCHEMA).file_links()
    assert links == [
        ("http://example.org/Film", "http://example.org/poster"),
        ("http://example.org/Screening", "http://example.org/flyer"),
    ]


def test_no_upload_shape_means_no_file_links(tmp_path: Path) -> None:
    without_upload = _RENAMED_UPLOAD_SCHEMA.replace(
        "sh:targetClass schema:DigitalDocument", "sh:targetClass ex:Poster"
    )
    assert _extractor(tmp_path, without_upload).file_links() == []
