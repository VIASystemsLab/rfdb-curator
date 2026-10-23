"""Tests for the upload-first digital-copy flow.

Covers the staging route (``api/files.py``), the write-path reconciliation +
promotion (``api/data.py``) and the cleanup reconciler
(``scripts/cleanup_files.py``) — i.e. the write half.

The download route and ``/api/v1/dataexplorer/meta/files`` belong to the read service; their
cases live in ``tests/dataexplorer/test_digital_copies_read.py``.

Uses a real ``rdflib.Graph`` behind a small in-memory store double (queries/updates/
constructs actually run) and the in-memory storage fake from
``rfdb_core.file_storage`` (no network, no ``moto``). Route handlers are called
directly, matching ``test_backend_api_data_unit.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from rdflib import XSD, Graph, Literal, URIRef

import api.data as data_mod
import api.files as files_mod
from api.data import _promote_staged_files, _reconcile_digital_copies
from api.files import download_staged_file, stage_file
from rfdb_core.file_storage import InMemoryStorage, registered_key, staged_key
from rfdb_core.files_state import file_content_url
from rfdb_core.schema_extractor import SchemaExtractor
from rfdb_core.vocab import SCHEMA_CONTENT_URL
from scripts.cleanup_files import NoFileLinks, reconcile

FILE_ID_RE = re.compile(r"^File_[0-9a-f]{8}$")
RFDB = "https://rosfeatr.eu/rdf/data/"
P138I = "http://www.cidoc-crm.org/cidoc-crm/P138i_has_representation"
DIGITAL_DOCUMENT = "http://schema.org/DigitalDocument"

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "rfdb_schema.ttl"
_EXTRACTOR = SchemaExtractor(str(_SCHEMA_PATH))


class _FakeOxigraph:
    """Oxigraph stub backed by a real in-memory rdflib graph (no named graph)."""

    def __init__(self) -> None:
        self.g = Graph()

    def from_clause(self) -> str:
        return ""

    def with_clause(self) -> str:
        return ""

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        res = self.g.query(sparql)
        return [{str(k): str(v) for k, v in b.items()} for b in res.bindings]

    def construct(self, sparql: str) -> Graph:
        return self.g.query(sparql).graph or Graph()

    def load_turtle(self, turtle: str) -> None:
        self.g.parse(data=turtle, format="turtle")

    def update(self, sparql: str) -> None:
        self.g.update(sparql)


def _make_pdf(pages: int = 1) -> bytes:
    """Return valid PDF bytes with the given number of blank pages."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _drain(resp) -> bytes:
    """Collect a StreamingResponse body (the staged-preview route streams)."""

    async def inner():
        return b"".join([chunk async for chunk in resp.body_iterator])

    return asyncio.run(inner())


@pytest.fixture
def env():
    """Fake store + storage + request wired like app.state."""
    store = _FakeOxigraph()
    storage = InMemoryStorage()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(store=store, storage=storage, schema_extractor=_EXTRACTOR)
        )
    )
    return SimpleNamespace(store=store, storage=storage, request=request)


# ---------------------------------------------------------------------------
# Staging route
# ---------------------------------------------------------------------------


def test_stage_non_pdf_rejected(env):
    """A non-PDF body is rejected with 415; nothing is stored."""
    with pytest.raises(HTTPException) as exc:
        stage_file(env.request, _upload("x.png", b"\x89PNG\r\n"))
    assert exc.value.status_code == 415
    assert env.storage.objects == {}


def test_stage_over_size_cap_rejected(env, monkeypatch):
    """A body over MAX_UPLOAD_MB is rejected with 413; nothing is stored."""
    monkeypatch.setattr(files_mod.settings, "max_upload_mb", 1)
    oversize = b"%PDF-" + b"\x00" * (1024 * 1024 + 10)
    with pytest.raises(HTTPException) as exc:
        stage_file(env.request, _upload("big.pdf", oversize))
    assert exc.value.status_code == 413
    assert env.storage.objects == {}


def test_stage_storage_error_propagates(env):
    """A backend StorageError is not swallowed — it reaches the app-level handler.

    The route must NOT convert it to a leaky 500/detailed message; app.py maps
    the StorageError family to a clean 503.
    """
    from rfdb_core.file_storage import StorageError, StorageNotInitialized

    def boom(*_a, **_k):
        raise StorageNotInitialized("simulated garage AccessDenied")

    env.storage.put_pdf = boom
    with pytest.raises(StorageError):  # base type — subclass propagates too
        stage_file(env.request, _upload("x.pdf", _make_pdf()))


def test_storage_error_classification():
    """Auth/bucket ClientErrors → NotInitialized; connectivity/other → Unavailable."""
    from botocore.exceptions import ClientError, EndpointConnectionError

    from rfdb_core.file_storage import (
        StorageNotInitialized,
        StorageUnavailable,
        _storage_error,
    )

    def client_error(code: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": "x"}}, "PutObject")

    # Not initialized — deterministic setup/permission faults.
    for code in ("AccessDenied", "NoSuchBucket", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
        assert isinstance(_storage_error("op", "ep", client_error(code)), StorageNotInitialized)

    # Unavailable — transient / connectivity / unknown service errors.
    assert isinstance(_storage_error("op", "ep", client_error("InternalError")), StorageUnavailable)
    assert isinstance(_storage_error("op", "ep", client_error("SlowDown")), StorageUnavailable)
    assert isinstance(
        _storage_error("op", "ep", EndpointConnectionError(endpoint_url="ep")),
        StorageUnavailable,
    )


def test_stage_read_only_blocked(env, monkeypatch):
    """READ_ONLY mode refuses staging with 403."""
    monkeypatch.setattr(data_mod.settings, "read_only", True)
    with pytest.raises(HTTPException) as exc:
        stage_file(env.request, _upload("x.pdf", _make_pdf()))
    assert exc.value.status_code == 403


def test_stage_returns_prefilled_node_and_stores_staged(env):
    """Staging stores under staged/ and returns correct derived metadata."""
    pdf = _make_pdf(pages=3)
    node = stage_file(env.request, _upload("libretto scan (1736).pdf", pdf))

    assert FILE_ID_RE.match(node.fileId)
    assert node.id == RFDB + node.fileId
    assert node.name == "libretto scan (1736).pdf"
    assert node.contentSize == len(pdf)
    assert node.sha256 == hashlib.sha256(pdf).hexdigest()
    assert node.numberOfPages == 3
    # The staged preview path on *this* service, not the reader's published one:
    # nothing references this file yet, so the reader would 404 it.
    assert node.contentUrl == f"/api/v1/curator/files/staged/{node.fileId}"
    assert env.storage.objects[staged_key(node.fileId)] == pdf
    # No triples written at staging time.
    assert len(env.store.g) == 0


# ---------------------------------------------------------------------------
# Staged preview download (curator-side; the reader refuses unreferenced files)
# ---------------------------------------------------------------------------


def test_download_staged_serves_the_staged_bytes(env):
    """The preview route returns exactly what was staged, named by file id.

    The original filename lives in unsubmitted form state on the client, not in
    RDF, so the id is all the server can honestly name it.
    """
    pdf = _make_pdf(pages=2)
    node = stage_file(env.request, _upload("libretto.pdf", pdf))

    resp = download_staged_file(node.fileId, env.request)
    assert resp.media_type == "application/pdf"
    assert f'filename="{node.fileId}.pdf"' in resp.headers["content-disposition"]
    assert _drain(resp) == pdf


def test_download_staged_matches_the_url_staging_handed_back(env):
    """The staging response's contentUrl is the path this route actually serves.

    A client joining ``contentUrl`` to the origin it just POSTed to must land on
    real bytes, with no cross-service knowledge.
    """
    node = stage_file(env.request, _upload("a.pdf", _make_pdf()))
    assert node.contentUrl == f"/api/v1/curator/files/staged/{node.fileId}"


def test_download_staged_404s_once_promoted(env):
    """After promotion the file is published — the reader serves it, not this route."""
    node = stage_file(env.request, _upload("a.pdf", _make_pdf()))
    env.storage.move(staged_key(node.fileId), registered_key(node.fileId))

    with pytest.raises(HTTPException) as exc:
        download_staged_file(node.fileId, env.request)
    assert exc.value.status_code == 404


def test_download_staged_unknown_or_malformed_404(env):
    """Unknown ids and malformed paths are refused before touching storage."""
    for bad in ("File_deadbeef", "not-a-file-id", "../etc/passwd"):
        with pytest.raises(HTTPException) as exc:
            download_staged_file(bad, env.request)
        assert exc.value.status_code == 404


def test_download_staged_read_only_blocked(env, monkeypatch):
    """READ_ONLY mode refuses the preview too: no submit workflow, no preview.

    Gated deliberately — in a read-only deployment this route would otherwise be
    a public window onto staged working state.
    """
    node = stage_file(env.request, _upload("a.pdf", _make_pdf()))
    monkeypatch.setattr(data_mod.settings, "read_only", True)

    with pytest.raises(HTTPException) as exc:
        download_staged_file(node.fileId, env.request)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Write-path reconciliation + promotion
# ---------------------------------------------------------------------------


def _copy_node_turtle(node, sha="0" * 64, size=1, name="x.pdf") -> str:
    """Payload-graph turtle for a digital-copy node with client-supplied values."""
    return f"""
        @prefix schema: <http://schema.org/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        <{node}> a schema:DigitalDocument ;
          schema:name "{name}" ;
          schema:encodingFormat "application/pdf" ;
          schema:contentUrl "/api/files/x"^^xsd:anyURI ;
          schema:contentSize {size} ;
          schema:sha256 "{sha}" .
    """


def test_reconcile_rederives_staged_metadata(env):
    """Tampered payload metadata is replaced with server-derived values."""
    pdf = _make_pdf(pages=2)
    node = stage_file(env.request, _upload("a.pdf", pdf))

    payload = Graph()
    payload.parse(data=_copy_node_turtle(node.id, sha="f" * 64, size=999), format="turtle")
    promote = _reconcile_digital_copies(payload, env.request)

    assert promote == [node.fileId]
    true_sha = hashlib.sha256(pdf).hexdigest()
    values = {str(o) for o in payload.objects(None, None)}
    assert true_sha in values  # server value in
    assert "f" * 64 not in values  # tampered value out
    assert str(len(pdf)) in values and "999" not in values
    # contentUrl recomputed to the *published* absolute URL, replacing the staged
    # preview path staging handed back: on persist the file stops being working
    # state and acquires a published identity, absolute and dereferenceable.
    assert file_content_url(node.fileId, RFDB) in values
    assert f"/api/v1/curator/files/staged/{node.fileId}" not in values


def test_reconciled_copy_passes_shacl_validation(env):
    """The graph the write path produces must **validate**, not merely look right.

    This is the guard that matters: an absolute ``contentUrl`` stored as an IRI
    dereferences perfectly, returns 200, and violates ``sh:datatype xsd:anyURI``,
    which admits only literals. Nothing about the response or the URL would tell
    you. Only pyshacl does.

    Asserting the *outcome* (this graph conforms) rather than the mechanism (the term
    is a literal) is deliberate: it stays true if the shape's constraints change, and
    it catches violations on the other derived fields too.
    """
    from pyshacl import validate  # noqa: PLC0415 — heavy import, only this test needs it

    node = stage_file(env.request, _upload("a.pdf", _make_pdf()))

    payload = Graph()
    payload.parse(data=_copy_node_turtle(node.id), format="turtle")
    _reconcile_digital_copies(payload, env.request)

    # A copy is only valid as something's representation, so give it a parent — the
    # same link predicate the schema declares (see SchemaExtractor.file_links).
    payload.add(
        (
            URIRef("https://rosfeatr.eu/rdf/data/probe_source"),
            URIRef("http://www.cidoc-crm.org/cidoc-crm/P138i_has_representation"),
            URIRef(node.id),
        )
    )

    shapes = Graph().parse(str(_SCHEMA_PATH), format="turtle")
    conforms, _, report = validate(payload, shacl_graph=shapes, advanced=True, inference="none")
    assert conforms, f"write path produced a SHACL-invalid digital copy:\n{report}"


def test_persisted_content_url_is_an_any_uri_literal(env):
    """The value must be an ``xsd:anyURI`` **literal**, not an IRI node.

    ``rfdb_schema.ttl`` declares ``sh:datatype xsd:anyURI`` on ``schema:contentUrl``, and
    ``sh:datatype`` is satisfied only by a literal — so writing an IRI here produces
    a triple that dereferences perfectly and fails validation. That combination is
    nasty to debug (the data *looks* right), and it is an easy mistake to make
    because the value is a full URL, which is why the term type is pinned rather than
    just the string. Making it an IRI is a defensible modelling change, but it is a
    schema change, and has to be done as one.
    """
    node = stage_file(env.request, _upload("a.pdf", _make_pdf()))

    payload = Graph()
    payload.parse(data=_copy_node_turtle(node.id), format="turtle")
    _reconcile_digital_copies(payload, env.request)

    urls = list(payload.objects(URIRef(node.id), URIRef(SCHEMA_CONTENT_URL)))
    assert len(urls) == 1, f"expected exactly one contentUrl, got {urls}"
    (url,) = urls
    assert isinstance(url, Literal), f"contentUrl must be a literal, got {type(url).__name__}"
    assert url.datatype == XSD.anyURI, f"expected xsd:anyURI, got {url.datatype}"
    assert str(url) == file_content_url(node.fileId, RFDB)


def test_reconcile_strips_registered_metadata(env):
    """Registered nodes keep only their type in the payload (store wins)."""
    pdf = _make_pdf()
    node = stage_file(env.request, _upload("a.pdf", pdf))
    env.storage.move(staged_key(node.fileId), registered_key(node.fileId))

    payload = Graph()
    payload.parse(data=_copy_node_turtle(node.id, sha="f" * 64), format="turtle")
    promote = _reconcile_digital_copies(payload, env.request)

    assert promote == []  # already registered — nothing to promote
    preds = {str(p) for p in payload.predicates(None, None)}
    assert preds == {"http://www.w3.org/1999/02/22-rdf-syntax-ns#type"}


def test_reconcile_missing_staged_file_422(env):
    """A payload node whose staged object vanished is rejected."""
    payload = Graph()
    payload.parse(data=_copy_node_turtle(f"{RFDB}File_deadbeef"), format="turtle")
    with pytest.raises(HTTPException) as exc:
        _reconcile_digital_copies(payload, env.request)
    assert exc.value.status_code == 422


def test_reconcile_bad_node_iri_422(env):
    """Digital-copy nodes outside the File_{8hex} namespace are rejected."""
    payload = Graph()
    payload.parse(
        data=f"<{RFDB}evil> a <{DIGITAL_DOCUMENT}> .",
        format="turtle",
    )
    with pytest.raises(HTTPException) as exc:
        _reconcile_digital_copies(payload, env.request)
    assert exc.value.status_code == 422


def test_promote_moves_staged_to_registered(env):
    """Promotion moves the object; a failed move is logged, not raised."""
    pdf = _make_pdf()
    node = stage_file(env.request, _upload("a.pdf", pdf))
    _promote_staged_files(env.request, [node.fileId, "File_00000000"])  # 2nd missing
    assert env.storage.objects == {registered_key(node.fileId): pdf}


# ---------------------------------------------------------------------------
# Cleanup reconciler (scripts/cleanup_files.py)
# ---------------------------------------------------------------------------


def _link(env, file_id: str, parent: str = f"{RFDB}S1") -> None:
    """Write the parent→file link + node type triples into the fake store."""
    env.store.load_turtle(
        f"<{parent}> <{P138I}> <{RFDB}{file_id}> . <{RFDB}{file_id}> a <{DIGITAL_DOCUMENT}> ."
    )


def test_reconcile_script_rules(env):
    """Promote referenced-staged; expire old staged; grace-delete registered; purge orphans."""
    clock = {"now": 1000.0}
    storage = InMemoryStorage(clock=lambda: clock["now"])
    env.request.app.state.storage = storage

    # a) staged + referenced (crash before promotion) → promote
    storage.put_pdf(staged_key("File_aaaaaaaa"), io.BytesIO(b"%PDF-a"))
    _link(env, "File_aaaaaaaa")
    # b) staged + unreferenced + old → delete
    storage.put_pdf(staged_key("File_bbbbbbbb"), io.BytesIO(b"%PDF-b"))
    # c) staged + unreferenced + fresh → keep
    # (created later, after the clock advances)
    # d) registered + unreferenced + old → delete
    storage.put_pdf(registered_key("File_dddddddd"), io.BytesIO(b"%PDF-d"))
    # e) registered + linked → keep
    storage.put_pdf(registered_key("File_eeeeeeee"), io.BytesIO(b"%PDF-e"))
    _link(env, "File_eeeeeeee")
    # f) orphaned node: typed but unlinked → triples purged + object deleted
    env.store.load_turtle(f"<{RFDB}File_ffffffff> a <{DIGITAL_DOCUMENT}> .")
    storage.put_pdf(registered_key("File_ffffffff"), io.BytesIO(b"%PDF-f"))

    clock["now"] = 1000.0 + 100_000  # everything above is now "old"
    storage.put_pdf(staged_key("File_cccccccc"), io.BytesIO(b"%PDF-c"))  # (c) fresh

    actions = reconcile(
        storage,
        env.store,
        _EXTRACTOR,
        data_base=RFDB,
        staged_ttl_s=86_400,
        registered_grace_s=86_400,
        now=clock["now"],
    )

    assert actions["promoted"] == ["File_aaaaaaaa"]
    assert actions["deleted_staged"] == ["File_bbbbbbbb"]
    assert actions["deleted_registered"] == ["File_dddddddd"]
    assert actions["purged_nodes"] == ["File_ffffffff"]
    assert set(storage.objects) == {
        registered_key("File_aaaaaaaa"),  # promoted
        staged_key("File_cccccccc"),  # fresh, kept
        registered_key("File_eeeeeeee"),  # linked, kept
    }
    # Orphaned node triples are gone from the store.
    assert (None, None, None) not in Graph().parse(
        data="", format="turtle"
    ) or f"{RFDB}File_ffffffff" not in {str(s) for s in env.store.g.subjects()}


def test_reconcile_script_dry_run_changes_nothing(env):
    """--dry-run reports actions but leaves storage and triples untouched."""
    clock = {"now": 1000.0}
    storage = InMemoryStorage(clock=lambda: clock["now"])
    storage.put_pdf(staged_key("File_bbbbbbbb"), io.BytesIO(b"%PDF-b"))
    clock["now"] += 100_000

    actions = reconcile(
        storage,
        env.store,
        _EXTRACTOR,
        data_base=RFDB,
        staged_ttl_s=86_400,
        registered_grace_s=86_400,
        now=clock["now"],
        dry_run=True,
    )
    assert actions["deleted_staged"] == ["File_bbbbbbbb"]
    assert staged_key("File_bbbbbbbb") in storage.objects  # still there


def test_reconcile_refuses_when_the_schema_declares_no_upload(env):
    """With no upload links, every file would look unreferenced: abort, touch nothing."""
    storage = InMemoryStorage()
    storage.put_pdf(registered_key("File_aaaaaaaa"), io.BytesIO(b"%PDF-a"))
    _link(env, "File_aaaaaaaa")
    no_uploads = SimpleNamespace(file_links=lambda: [])

    with pytest.raises(NoFileLinks):
        reconcile(
            storage,
            env.store,
            no_uploads,
            data_base=RFDB,
            staged_ttl_s=0,
            registered_grace_s=0,
            now=10**9,
        )
    assert registered_key("File_aaaaaaaa") in storage.objects
    assert f"{RFDB}File_aaaaaaaa" in {str(s) for s in env.store.g.subjects()}
