"""Read half of the digital-copy flow: download the bytes, report the stats.

The write half is in ``tests/curator/test_digital_copies.py``.

Fixtures put objects into storage **directly** rather than by calling
``stage_file``. The reader has no staging route to call, and setting up through
the writer would quietly make these tests depend on it, which is the coupling the
writer/reader split exists to remove.

Uses a real ``rdflib.Graph`` behind a small store stub (queries actually run) and
the in-memory storage fake from ``rfdb_core.file_storage`` — no network, no
``moto``. Route handlers are called directly.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rdflib import Graph

import api.meta as meta_mod
from api.files import FILE_STATE_HEADER, download_file
from api.meta import get_file_stats
from rfdb_core.file_storage import (
    InMemoryStorage,
    registered_key,
    staged_key,
)
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"

RFDB = "https://rosfeatr.eu/rdf/data/"
P138I = "http://www.cidoc-crm.org/cidoc-crm/P138i_has_representation"
DIGITAL_DOCUMENT = "http://schema.org/DigitalDocument"

_EXTRACTOR = SchemaExtractor(str(_SCHEMA_PATH))


class _FakeStore:
    """Store stub backed by a real in-memory rdflib graph (no named graph)."""

    def __init__(self) -> None:
        self.g = Graph()

    def from_clause(self) -> str:
        return ""

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        res = self.g.query(sparql)
        return [{str(k): str(v) for k, v in b.items()} for b in res.bindings]

    def load_turtle(self, turtle: str) -> None:
        """Test-fixture only. The read service never calls this at runtime."""
        self.g.parse(data=turtle, format="turtle")


def _make_pdf(pages: int = 1) -> bytes:
    """Return valid PDF bytes with the given number of blank pages.

    pypdf is a *curator* runtime dependency (page counts are derived once, on
    write); dataexplorer-backend only pulls it into its dev dependency group,
    for fixture construction, so it is imported lazily here — the read service
    itself never parses a PDF.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def env():
    """Fake store + storage + request wired like the reader's app.state.

    Exactly the three entries dataexplorer-backend puts on app.state. If a
    handler needs a fourth, this fixture fails, and so should
    ``test_lifespan_sets_exactly_three_state_entries`` in ``test_app_contract.py``.
    """
    store = _FakeStore()
    storage = InMemoryStorage()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(store=store, storage=storage, schema_extractor=_EXTRACTOR)
        )
    )
    return SimpleNamespace(store=store, storage=storage, request=request)


def _stage(env, file_id: str, data: bytes) -> None:
    """Put bytes at the staged key, as the curator's upload route would."""
    env.storage.put_pdf(staged_key(file_id), io.BytesIO(data))


def _link(env, file_id: str, parent: str = f"{RFDB}S1") -> None:
    """Write the parent→file link + node type triples into the fake store."""
    env.store.load_turtle(
        f"<{parent}> <{P138I}> <{RFDB}{file_id}> . <{RFDB}{file_id}> a <{DIGITAL_DOCUMENT}> ."
    )


def _unreachable(_sparql):
    """Store stand-in for an outage: every query raises."""
    raise RuntimeError("store unreachable")


def _drain(resp) -> bytes:
    async def inner():
        return b"".join([chunk async for chunk in resp.body_iterator])

    return asyncio.run(inner())


# ---------------------------------------------------------------------------
# Download route
# ---------------------------------------------------------------------------


def test_download_registered_referenced(env):
    """The normal case: a referenced file, bytes in registered/, state header set."""
    pdf = _make_pdf()
    file_id = "File_0123abcd0123"
    _stage(env, file_id, pdf)
    env.storage.move(staged_key(file_id), registered_key(file_id))
    _link(env, file_id)

    resp = download_file(file_id, env.request)
    assert resp.media_type == "application/pdf"
    assert resp.headers[FILE_STATE_HEADER] == "registered"
    assert _drain(resp) == pdf


def test_download_unreferenced_staged_is_404(env):
    """Staged bytes nobody references are not a published resource.

    The reader is the public surface; a file becomes public when a parent entity
    references it. Serving unsubmitted working state here would make a
    legitimately staged file indistinguishable from a broken promotion, and would
    hand out content that was never submitted. Curators preview their own uploads
    through the writer instead.
    """
    file_id = "File_0123abcd0123"
    _stage(env, file_id, _make_pdf())  # staged, never linked

    with pytest.raises(HTTPException) as exc:
        download_file(file_id, env.request)
    assert exc.value.status_code == 404


def test_download_unreferenced_registered_is_404(env):
    """An orphaned registered object (entity deleted, GC pending) is also not public."""
    file_id = "File_0123abcd0123"
    env.storage.put_pdf(registered_key(file_id), io.BytesIO(_make_pdf()))

    with pytest.raises(HTTPException) as exc:
        download_file(file_id, env.request)
    assert exc.value.status_code == 404


def test_download_awaiting_promotion_is_served_but_flagged(env, caplog):
    """Referenced but still staged: a failed promotion downloads, loudly.

    Serving from whichever storage prefix holds the bytes would hide this case.
    The entity write
    succeeded, so the file *is* published and must not 404 — but the promotion
    that should have moved it did not run, and that has to be visible rather than
    waiting for someone to notice the reconciler is behind.
    """
    pdf = _make_pdf()
    file_id = "File_0123abcd0123"
    _stage(env, file_id, pdf)
    _link(env, file_id)  # persisted, but never promoted

    with caplog.at_level("WARNING"):
        resp = download_file(file_id, env.request)

    assert resp.headers[FILE_STATE_HEADER] == "awaiting-promotion"
    assert _drain(resp) == pdf
    assert "promotion" in caplog.text
    assert file_id in caplog.text


def test_download_referenced_with_missing_bytes_is_404(env, caplog):
    """Referenced in RDF but absent from both prefixes: genuine data loss, logged."""
    file_id = "File_0123abcd0123"
    _link(env, file_id)  # no object staged or registered

    with caplog.at_level("ERROR"), pytest.raises(HTTPException) as exc:
        download_file(file_id, env.request)
    assert exc.value.status_code == 404
    assert file_id in caplog.text


def test_download_unknown_id_404(env):
    """Unknown or malformed file ids return 404."""
    for bad in ("File_deadbeefdead", "not-a-file-id", "../etc/passwd"):
        with pytest.raises(HTTPException) as exc:
            download_file(bad, env.request)
        assert exc.value.status_code == 404


def test_download_filename_from_rdf(env):
    """A persisted node's schema:name becomes the attachment filename."""
    file_id = "File_0123abcd0123"
    _stage(env, file_id, _make_pdf())
    env.storage.move(staged_key(file_id), registered_key(file_id))
    _link(env, file_id)
    env.store.load_turtle(f'<{RFDB}{file_id}> <http://schema.org/name> "scan.pdf" .')

    resp = download_file(file_id, env.request)
    assert 'filename="scan.pdf"' in resp.headers["content-disposition"]


def test_registered_file_survives_an_unreachable_store(env):
    """Store down, bytes in registered/: still served, flagged unverified.

    The bytes live in object storage, not the triplestore, so an outage must not
    turn a published download into a 503. Presence under ``registered/`` is
    evidence enough on its own — objects only land there via a promotion, and a
    promotion only ever follows a successful entity write.
    """
    file_id = "File_0123abcd0123"
    pdf = _make_pdf()
    env.storage.put_pdf(registered_key(file_id), io.BytesIO(pdf))
    env.store.query = _unreachable

    resp = download_file(file_id, env.request)
    assert resp.headers[FILE_STATE_HEADER] == "registered-unverified"
    assert _drain(resp) == pdf
    assert f'filename="{file_id}.pdf"' in resp.headers["content-disposition"]


def test_staged_file_is_refused_when_the_store_is_unreachable(env):
    """Store down, bytes only in staged/: 503 rather than guess.

    Failing open here would serve unsubmitted working state during exactly the
    outage nobody is watching. Nothing about a ``staged/`` object says it was ever
    submitted, so the honest answer is "cannot verify", not the bytes.
    """
    file_id = "File_0123abcd0123"
    _stage(env, file_id, _make_pdf())
    env.store.query = _unreachable

    with pytest.raises(HTTPException) as exc:
        download_file(file_id, env.request)
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Stats endpoint
# ---------------------------------------------------------------------------


def test_file_stats_shape(env, monkeypatch):
    """/api/v1/dataexplorer/meta/files reports counts, bytes, and orphan indicators."""
    monkeypatch.setattr(meta_mod.settings, "s3_endpoint", "http://garage:3900")
    pdf = _make_pdf()
    _stage(env, "File_aaaaaaaaaaaa", pdf)  # staged, unreferenced
    env.storage.put_pdf(registered_key("File_eeeeeeeeeeee"), io.BytesIO(b"%PDF-e"))
    _link(env, "File_eeeeeeeeeeee")

    stats = get_file_stats(env.request)
    assert stats["configured"] is True
    assert stats["staged"]["count"] == 1
    assert stats["staged"]["bytes"] == len(pdf)
    assert stats["staged"]["oldestAgeS"] is not None
    assert stats["registered"]["count"] == 1
    assert stats["linkedNodes"] == 1
    assert stats["unreferencedStaged"] == 1
    assert stats["unreferencedRegistered"] == 0
    assert stats["orphanedNodes"] == 0


def test_file_stats_unconfigured(env, monkeypatch):
    """Without S3_ENDPOINT the endpoint reports configured: false, zeroed."""
    monkeypatch.setattr(meta_mod.settings, "s3_endpoint", "")
    stats = get_file_stats(env.request)
    assert stats["configured"] is False
    assert stats["staged"]["count"] == 0
