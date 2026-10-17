"""Upload-first digital-copy routes: stage a PDF for a pending entity write.

A digital copy is a bridge node whose fields are machine-filled. The flow:

1. ``POST /api/v1/curator/files/staged`` — parent-agnostic. Sniffs the PDF, computes
   sha256/size/pages, stores the bytes under ``staged/File_{8hex}.pdf`` and
   returns the prefilled ``schema:DigitalDocument`` node. The form shows it as a
   read-only bridge entry — on unsaved forms too.
2. ``GET /api/v1/curator/files/staged/{file_id}`` — preview of a file staged but not yet
   submitted. It lives here, not on the reader, because staged bytes are one
   curator's working state: nothing outside this service should hand them out.
   That is also why the staging response's ``contentUrl`` points here, and so
   resolves against the very origin the client just POSTed to.
3. The node travels inside the normal JSON-LD payload of ``POST /api/v1/curator/entities``
   under whatever predicate the *schema* declares (any shape with a property
   whose ``sh:node`` shape targets ``schema:DigitalDocument`` gets the widget). The write
   path re-derives the metadata server-side — including ``contentUrl``, which it
   rewrites to the published path — and promotes the object to ``registered/``
   on persist. See ``api/data.py``.
4. ``GET /rdf/data/{file_id}/content`` streams a *published* copy back. That step is
   served by **dataexplorer-backend** — handing bytes out is a read. It refuses
   anything no parent entity references, which is why step 2 exists.

Abandoned staged files and unreferenced registered ones are collected by
``scripts/cleanup_files.py`` — RDF is the source of truth; storage is
reconciled against it, never the other way around. The read-only snapshot both
that script and ``/api/v1/dataexplorer/meta/files`` work from lives in
``rfdb_core.files_state``, since it is needed on both sides of the split.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.data import _assert_writable_mode
from core.config import settings
from models.files import DigitalCopy
from rfdb_core.file_storage import staged_key
from rfdb_core.files_state import FILE_ID_RE, staged_content_url
from rfdb_core.vocab import RFDB_BASE

router = APIRouter()
logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF-"
_CHUNK = 1024 * 1024  # 1 MiB


def _page_count(fileobj) -> int | None:
    """PDF page count via pypdf; ``None`` for encrypted/damaged files."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(fileobj).pages)
    except Exception as exc:  # pypdf raises a variety of types on bad input
        logger.warning("Could not read PDF page count: %s", exc)
        return None


def derive_metadata(spool) -> tuple[int, str, int | None]:
    """Return ``(size, sha256, pages)`` for a spooled PDF, streaming in chunks.

    Enforces ``MAX_UPLOAD_MB`` while reading so an oversize body is rejected
    without hashing it in full. Shared by the staging route and the write-path
    re-derivation in ``api/data.py`` (server is the metadata authority).

    Raises:
        HTTPException 413: size exceeds ``MAX_UPLOAD_MB`` (0 disables the cap).
    """
    max_bytes = settings.max_upload_mb * 1024 * 1024 if settings.max_upload_mb else None
    hasher = hashlib.sha256()
    size = 0
    spool.seek(0)
    while chunk := spool.read(_CHUNK):
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {settings.max_upload_mb} MB upload limit",
            )
        hasher.update(chunk)
    spool.seek(0)
    pages = _page_count(spool)
    return size, hasher.hexdigest(), pages


@router.post("/files/staged", response_model=DigitalCopy)
def stage_file(request: Request, file: UploadFile) -> DigitalCopy:
    """Stage an uploaded PDF and return the prefilled digital-copy node.

    No triples are written here — the returned node is form-state only until
    the curator submits the parent entity. Unsubmitted staged files expire via
    the cleanup script.

    Raises:
        403: Read-only mode.
        413: File exceeds ``MAX_UPLOAD_MB``.
        415: Body is not a PDF.
        503: Storage not configured.
    """
    _assert_writable_mode()

    spool = file.file  # SpooledTemporaryFile — sync route, so direct access is fine
    spool.seek(0)
    if spool.read(len(_PDF_MAGIC)) != _PDF_MAGIC:
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")

    size, sha256, pages = derive_metadata(spool)

    # Random 8-hex id (assign_entity_id idiom): never reused, no mint race.
    file_id = f"File_{uuid.uuid4().hex[:8]}"
    original_name = os.path.basename(file.filename or "") or f"{file_id}.pdf"

    spool.seek(0)
    # A StorageError here (endpoint down, bad creds, missing bucket) propagates
    # to the app-level handler → clean 503. See rfdb_core.app_factory.
    request.app.state.storage.put_pdf(staged_key(file_id), spool)

    return DigitalCopy(
        id=RFDB_BASE + file_id,
        fileId=file_id,
        name=original_name,
        # The staged preview path, not the published one: this file is not a
        # public resource yet, and the reader would (correctly) 404 it. The
        # submit path rewrites this to file_content_url() when it persists.
        contentUrl=staged_content_url(file_id),
        contentSize=size,
        sha256=sha256,
        numberOfPages=pages,
    )


@router.get("/files/staged/{file_id}")
def download_staged_file(file_id: str, request: Request):
    """Stream a staged, not-yet-submitted PDF back to the curator who staged it.

    The counterpart of ``stage_file``: it lets the form preview an upload before
    the parent entity exists. The reader cannot serve this — a staged file is
    referenced by nothing, so it is not a published resource — and that
    separation is the point. Mode-gated like the upload it pairs with: with no
    curator workflow to submit through, there is no preview to serve either.

    Raises:
        403: Read-only mode.
        404: Malformed id, or no staged object (submitted already, or expired).
        503: Storage not configured.
    """
    _assert_writable_mode()

    if not FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=404, detail="File not found")

    # A StorageError propagates to the app-level handler → clean 503.
    try:
        stream = request.app.state.storage.open_stream(staged_key(file_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found") from None

    # The original filename lives in unsubmitted form state on the client, not
    # in RDF, so there is nothing to look up here — the id is the honest name.
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_id}.pdf"'},
    )
