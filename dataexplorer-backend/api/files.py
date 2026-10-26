"""Digital-copy download: stream a *published* PDF back to the client.

Mounted as ``GET /rdf/data/{file_id}/content`` — inside the stable data space, not
under ``/api``, because the bytes are a **representation of the resource** whose
IRI is ``/rdf/data/{file_id}``. This is the URL that ``schema:contentUrl``
points at, so it is a persisted identifier and must never move.

The read half of the upload-first digital-copy flow. Staging (``POST
/files/staged``), the server-side metadata re-derivation and the promotion to
``registered/`` belong to curator-backend; this service only ever hands bytes
out.

Two questions get answered here, and keeping them apart is the whole design:

1. **Is this a published resource?** An RDF question, answered by
   ``is_file_referenced``. Bytes with no parent entity pointing at them are one
   curator's unsubmitted working state, not something this public service
   publishes, so they are refused. Curators preview their own staged uploads
   through the writer's ``GET /api/v1/curator/files/staged/{file_id}`` instead —
   an application route, not a published identifier, which is why it stays in the
   ``/api`` space while this one does not.
2. **Where do the bytes live?** A storage detail. ``registered/`` is tried
   first, then ``staged/``, because a promotion can fail *after* the entity
   write succeeds (the write path logs a warning and leaves the object for
   ``curator-backend/scripts/cleanup_files.py``). Such a file is published and
   must download, but it is also a fault, so it is served with
   ``X-RFDB-File-State: awaiting-promotion`` and a logged warning rather than
   silently, which keeps a stalled reconciler visible.

(1) is decided on RDF, never on the storage prefix, because the prefix cannot
tell those two states apart: an unsubmitted upload and a published file whose
promotion broke both sit under ``staged/``.

Downloads are proxied through the backend (never a direct browser→Garage path)
so Garage can stay on the internal network in production.

Note what is *absent*: ``assert_writable_mode``. The curator's ``api/files.py``
imports that from ``api.data`` to guard its routes. Nothing here mutates, so
there is no mode to assert — and no import back into the write service.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.config import settings
from rfdb_core.file_storage import registered_key, staged_key
from rfdb_core.files_state import FILE_ID_RE, is_file_referenced
from rfdb_core.vocab import SCHEMA_NAME

router = APIRouter()
logger = logging.getLogger(__name__)

# Lifecycle state of the bytes being served, so a client (or an operator reading
# access logs) can tell a healthy download from one exposing a stalled promotion.
FILE_STATE_HEADER = "X-RFDB-File-State"


def _rdf_name(store, file_id: str) -> str | None:
    """The node's ``schema:name``, or ``None`` if absent or the store is down."""
    try:
        rows = store.query(
            f"SELECT ?name {store.from_clause()} "
            f"WHERE {{ <{settings.data_base_uri}{file_id}> <{SCHEMA_NAME}> ?name . }} LIMIT 1"
        )
        return rows[0]["name"] if rows else None
    except Exception:  # store went down between this and the reference check
        return None


def _stream(storage, key: str, name: str, state: str) -> StreamingResponse:
    """Stream ``key`` as a PDF attachment named ``name``.

    Raises:
        FileNotFoundError: no object at ``key``.
    """
    stream = storage.open_stream(key)
    # Strip quotes, backslashes and control characters (a crafted name with
    # \r\n would otherwise be rejected by the HTTP stack → 500).
    safe_name = "".join(c for c in name if c.isprintable() and c not in '"\\') or "download.pdf"
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            FILE_STATE_HEADER: state,
        },
    )


def _serve_unverifiable(file_id: str, request: Request) -> StreamingResponse:
    """Serve during a triplestore outage, on the weaker evidence storage can give.

    With RDF unreachable there is no way to ask whether the file is published, so
    we fall back to the one thing the storage layout attests: bytes under
    ``registered/`` only ever get there via a promotion, which only ever follows
    a successful entity write. That makes them a published resource by history.
    Bytes under ``staged/`` prove nothing of the kind, so they are refused —
    failing open on them would leak unsubmitted working state during precisely
    the outage nobody is watching.

    One accepted asymmetry: an orphaned ``registered/`` object (its entity was
    deleted, GC has not run) is a 404 while the store is up and a 200 while it is
    down. Inherent to failing open, and bounded by the reconciler's grace window.

    Raises:
        HTTPException 503: nothing under ``registered/`` to fall back on.
    """
    storage = request.app.state.storage
    key = registered_key(file_id)
    try:
        response = _stream(storage, key, f"{file_id}.pdf", "registered-unverified")
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="Cannot verify this file while the triplestore is unreachable.",
        ) from None
    logger.warning(
        "Serving '%s' from %s without an RDF check — the triplestore is unreachable.",
        file_id,
        key,
    )
    return response


@router.get("/data/{file_id}/content")
def download_file(file_id: str, request: Request):
    """Stream a published digital copy as a PDF attachment.

    Mounted under ``/rdf``, so the full path is ``/rdf/data/{file_id}/content``.

    Responds ``X-RFDB-File-State: registered`` normally,
    ``awaiting-promotion`` when the bytes are still staged (a fault — see the
    module docstring), or ``registered-unverified`` when the store was down.

    Raises:
        HTTPException 404: malformed id, not referenced by any parent entity
            (unsubmitted or orphaned), or referenced with its bytes missing.
        HTTPException 503: the triplestore is unreachable and there is no
            registered object to serve without it.
    """
    if not FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=404, detail="File not found")

    store = request.app.state.store
    try:
        published = is_file_referenced(
            file_id, store, request.app.state.schema_extractor, settings.data_base_uri
        )
    except Exception as exc:  # store unreachable, or a malformed query
        logger.warning("Could not check whether '%s' is referenced: %s", file_id, exc)
        return _serve_unverifiable(file_id, request)

    if not published:
        # Unsubmitted form state, or an orphan awaiting GC. Either way not a
        # resource this service publishes — and storage is never touched.
        raise HTTPException(status_code=404, detail="File not found")

    storage = request.app.state.storage
    name = _rdf_name(store, file_id) or f"{file_id}.pdf"
    for key, state in (
        (registered_key(file_id), "registered"),
        (staged_key(file_id), "awaiting-promotion"),
    ):
        try:
            response = _stream(storage, key, name, state)
        except FileNotFoundError:
            continue  # a StorageError would propagate to the app handler → 503
        if state == "awaiting-promotion":
            logger.warning(
                "File '%s' is referenced in RDF but its bytes are still staged — "
                "the promotion after the entity write did not complete. "
                "curator-backend/scripts/cleanup_files.py will move it on its next run.",
                file_id,
            )
        return response

    # Referenced by an entity but the bytes are gone from both prefixes.
    logger.error("Referenced file '%s' has no object in storage.", file_id)
    raise HTTPException(status_code=404, detail="File not found")
