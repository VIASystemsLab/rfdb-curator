"""Read-side view of digital-copy state: RDF vs. object storage.

Everything here is read-only, and it is needed on both sides of the writer/reader
split, which is why it lives in the shared library:

  * the reader's ``GET /api/v1/dataexplorer/meta/files`` renders these counts in the Data
    Context Panel;
  * the curator's ``scripts/cleanup_files.py`` reconciler acts on them;
  * the reader's download route asks :func:`is_file_referenced` whether a file
    is a published resource at all, and the curator's staging response hands
    back :func:`staged_content_url`.

The second consumer is the reason this cannot live in the read service: the
cleanup script is a curator-side maintenance tool, so putting the snapshot in
``dataexplorer-backend`` would make the writer depend on the reader.

RDF is the source of truth throughout — storage is compared against it, never
the other way around. That direction is load-bearing for the download route:
which prefix holds a file's bytes is a storage detail that can lag RDF, so it
must never be used to decide whether the file is published.
"""

from __future__ import annotations

import re

from rfdb_core.file_storage import REGISTERED_PREFIX, STAGED_PREFIX
from rfdb_core.vocab import RFDB_BASE, SCHEMA_DIGITAL_DOCUMENT

# The minted file-id format: `File_` + 8 lowercase hex (see the curator's
# stage_file). Both services validate against it — the writer to reject a
# malformed digital-copy node IRI in a payload, the reader to reject a download
# path before touching storage — so the two must not be allowed to disagree.
FILE_ID_RE = re.compile(r"^File_[0-9a-f]{8}$")


def file_content_url(file_id: str) -> str:
    """Absolute, dereferenceable ``schema:contentUrl`` for a published copy.

    Built from ``RFDB_BASE`` — the same constant that mints the entity's own IRI
    — so a copy's bytes are addressed as a *representation of the resource*:
    ``https://rosfeatr.eu/rdf/data/File_x/content``.

    Absolute on purpose, which **reverses** this function's earlier reasoning
    that a persisted value "must not bake in a host" (decision D9). That was
    right while the value was an application path and the host was a deployment
    detail; it is wrong for a published identifier, where the host is part of the
    identity. The payoff: an exported graph is self-contained and correct with no
    base-URL side channel, and the URL resolves by being visited.

    Note for clients: this is the **identifier**. Any deployment whose reader is
    not on ``RFDB_BASE``'s host — every dev stack — must re-base the path onto
    its own read origin instead of fetching this URL as-is.

    Staged files are not published and have their own path — see
    :func:`staged_content_url`.
    """
    return f"{RFDB_BASE}{file_id}/content"


def staged_content_url(file_id: str) -> str:
    """Curator-relative preview path for a staged, not-yet-submitted file.

    Separate from :func:`file_content_url` because the two live on different
    services: staged bytes are one curator's in-progress working state, served
    by the writer that accepted them, while a registered file is a published
    resource served by the reader. Keeping them apart is what lets the reader
    refuse unsubmitted content outright.

    Only ever returned in the staging response — never persisted. The submit
    path re-derives ``schema:contentUrl`` with :func:`file_content_url`, so what
    lands in RDF is always the published path.

    Still **relative**, deliberately, now that :func:`file_content_url` is
    absolute: a staged file is not a published resource and has no identity to
    publish — it is one curator's working state on one service. That contrast is
    now load-bearing on the client, which picks a base by whether the value is
    absolute (published → the read origin) or relative (staged → this writer).
    """
    return f"/api/v1/curator/files/staged/{file_id}"


def is_file_referenced(file_id: str, store, extractor) -> bool:
    """Whether a parent entity links to this file via a schema-declared predicate.

    This is the "is it a published resource?" test, and RDF answers it. A file's
    storage prefix cannot: ``staged/`` vs ``registered/`` is only where the bytes
    happen to sit, and that lags behind RDF whenever a promotion fails — which
    the write path tolerates by design, logging a warning and leaving the object
    for the reconciler. Deciding on the prefix instead would make a broken
    promotion indistinguishable from a file that was never submitted.

    Uses the same notion of "linked" as :func:`collect_file_state` (reachable
    from a parent via a schema-declared link predicate), so the download route
    and ``scripts/cleanup_files.py`` cannot disagree about what counts as
    referenced.

    Args:
        file_id: A ``File_xxxxxxxx`` id.
        store: A ``TripleStore``; the read is scoped via ``from_clause()``.
        extractor: A ``SchemaExtractor``, used to resolve which predicates link
            a parent entity to a digital copy.

    Raises:
        ValueError: ``file_id`` is malformed. Checked because the id is
            interpolated into SPARQL; callers reaching the store must not be
            able to smuggle a term through.
    """
    if not FILE_ID_RE.match(file_id):
        raise ValueError(f"Malformed file id: {file_id!r}")

    link_preds = [pred for _, pred in extractor.file_links()]
    if not link_preds:
        # No shape links to a digital copy: nothing can be referenced at all.
        return False

    values = " ".join(f"<{pred}>" for pred in link_preds)
    rows = store.query(
        f"SELECT ?parent {store.from_clause()} WHERE {{ "
        f"VALUES ?p {{ {values} }} ?parent ?p <{RFDB_BASE}{file_id}> . "
        f"}} LIMIT 1"
    )
    return bool(rows)


def key_file_id(key: str, prefix: str) -> str:
    """File id from an object key: ``staged/File_x.pdf`` → ``File_x``."""
    return key[len(prefix) :].removesuffix(".pdf")


def collect_file_state(storage, store, extractor) -> dict:
    """Snapshot RDF and storage state for the reconciler and the stats endpoint.

    RDF is the source of truth: ``linked`` are file ids reachable from a parent
    via any schema-declared link predicate; ``typed`` are all
    ``schema:DigitalDocument`` subjects (typed-but-unlinked = orphaned node).
    ``staged``/``registered`` are the raw object listings.

    Args:
        storage: A ``FileStorage`` (``rfdb_core.file_storage``).
        store: A ``TripleStore`` (``rfdb_core.triplestore``); reads are scoped
            to the configured graph via ``from_clause()``.
        extractor: A ``SchemaExtractor``, used to resolve which predicates link
            a parent entity to a digital copy.

    Raises:
        StorageNotConfigured: storage credentials are absent.
    """
    linked: set[str] = set()
    for _, link_pred in extractor.file_links():
        rows = store.query(
            f"SELECT ?file {store.from_clause()} WHERE {{ ?parent <{link_pred}> ?file . }}"
        )
        linked |= {
            r["file"][len(RFDB_BASE) :] for r in rows if (r.get("file") or "").startswith(RFDB_BASE)
        }

    rows = store.query(
        f"SELECT ?n {store.from_clause()} WHERE {{ ?n a <{SCHEMA_DIGITAL_DOCUMENT}> . }}"
    )
    typed = {r["n"][len(RFDB_BASE) :] for r in rows if (r.get("n") or "").startswith(RFDB_BASE)}

    return {
        "linked": linked,
        "typed": typed,
        "staged": storage.list(STAGED_PREFIX),
        "registered": storage.list(REGISTERED_PREFIX),
    }
