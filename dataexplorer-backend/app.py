"""FastAPI application entrypoint for the RossijskijFeatrDB read backend.

This service answers queries and never mutates the store. What that buys is
mostly negative, and the absences are the design:

  * **No SHACL validator.** Validation exists to gate writes.
  * **No seeder, no reset.** It never loads or clears anything; the store arrives
    populated (by curator-backend's startup, or by
    ``curator-backend/scripts/seed.py`` run as a one-shot job).
  * **No shape dependency graph.** That plans the CONSTRUCT for merge-on-write.
  * **No ``READ_ONLY`` switch.** There is nothing to switch off.

Startup sequence (lifespan context manager):
  1. Parse ``schema/rfdb_schema.ttl`` into a ``SchemaExtractor``.
  2. Build a ``TripleStore`` via ``build_triplestore(settings)`` (Oxigraph today).
  3. Build the object-storage client for digital-copy downloads.

Exactly three objects land on ``app.state`` — ``schema_extractor``, ``store``,
``storage`` — which is what the requirement matrix in the modular-services plan
measured across the six read routers. Anything a handler needs beyond those three
is a sign it belongs in the writer.

Note — no readiness poll here, unlike curator-backend:
    The writer polls ``health()`` at startup because it is about to clear and
    seed, and a refused connection mid-wipe is unrecoverable. This service only
    reads, so an unreachable store is a *per-request* condition: handlers return
    503 (or empty results), ``/health`` reports ``store: "down"``, and the
    service recovers on its own once Oxigraph answers. Blocking startup on the
    store would make the reader less available, not more correct.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from rdflib.plugins.parsers.notation3 import BadSyntax

from api.data import router as data_router
from api.entities import router as entities_router
from api.files import router as files_router
from api.graph import router as graph_router
from api.meta import router as meta_router
from api.resource import router as resource_router
from api.shapes import router as shapes_router
from core.config import settings
from rfdb_core.app_factory import configure_app
from rfdb_core.file_storage import build_storage
from rfdb_core.logging_config import configure_logging
from rfdb_core.schema_extractor import SchemaExtractor
from rfdb_core.triplestore import build_triplestore

# Configure structured file + console logging before anything else runs.
configure_logging(
    settings.log_file,
    settings.log_level,
    truncate_on_startup=settings.truncate_log_on_startup,
    truncate_on_fresh_container_start=settings.truncate_log_on_fresh_container_start,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the three shared services before the server accepts requests.

    All work is synchronous and blocking, which is safe because no requests are
    served until after ``yield``. Nothing needs tearing down: Oxigraph and Garage
    are separate containers, and boto3's client is lazy.

    A malformed schema aborts startup — the shape metadata is the map every read
    route navigates by, so serving requests without it would return confidently
    wrong answers rather than errors.
    """
    logger.info("Starting up RossijskijFeatrDB dataexplorer backend…")

    try:
        app.state.schema_extractor = SchemaExtractor(
            settings.schema_path, settings.long_text_predicates, settings.external_id_links
        )
    except BadSyntax as exc:
        logger.error(
            "Invalid Turtle syntax in schema '%s'. Details: %s",
            settings.schema_path,
            exc,
            exc_info=True,
        )
        raise
    logger.info(
        "Schema loaded from '%s' (version %s).",
        settings.schema_path,
        app.state.schema_extractor.version or "unknown",
    )
    if app.state.schema_extractor.version is None:
        logger.warning(
            "Schema '%s' states no version (owl:versionInfo on an owl:Ontology node).",
            settings.schema_path,
        )
    if not app.state.schema_extractor.prefixes:
        logger.warning(
            "Schema '%s' declares no prefixes (sh:declare); IRIs will not be compacted.",
            settings.schema_path,
        )

    # Built through the seam's factory, keyed on settings.triplestore, so swapping
    # stores is a config change rather than an edit here.
    app.state.store = build_triplestore(settings)
    logger.info(
        "Triplestore '%s' initialised (base_url='%s').",
        settings.triplestore,
        settings.oxigraph_url,
    )

    # Object storage for source PDFs. The boto3 client connects lazily on first
    # use, so this never blocks startup or fails on an unreachable endpoint.
    app.state.storage = build_storage(settings)
    logger.info("File storage initialised (endpoint='%s').", settings.s3_endpoint or "<unset>")

    logger.info("Startup complete — accepting requests.")
    yield


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RossijskijFeatrDB Read API",
    description="Read-only query and publishing backend for the Russian Theatre DB.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS guard + middleware, the StorageError -> 503 handler, and the api.access
# request log — all shared with curator-backend so the two services behave
# identically at the edges.
configure_app(app, cors_origins=settings.cors_origins)

# Two URL spaces with different contracts (D8).
#
#   /rdf/…                     THE DATA. Stable forever, unversioned, public
#                              identifiers — this is what schema:contentUrl and
#                              every entity IRI point at, so nothing here may move.
#   /api/v1/dataexplorer/…     This service's operational surface for our own apps.
#                              Versioned, and named after its owner so no path can
#                              mean two things depending on which service answers.
API_PREFIX = "/api/v1/dataexplorer"
RDF_PREFIX = "/rdf"

# ---- the data space -------------------------------------------------------
# files_router first: /rdf/data/{id}/content is more specific than the
# dereference route, and registering it first keeps that true regardless of how
# either path is later edited.
app.include_router(files_router, prefix=RDF_PREFIX, tags=["data"])
app.include_router(resource_router, prefix=RDF_PREFIX, tags=["data"])

# ---- the operational surface ----------------------------------------------
# Order is NOT load-bearing here any more, and that is the point. It used to be:
# data_router owned a greedy `/entities/{entity_id:path}` that swallowed
# entities_router's `/entities/search` unless this block registered them in
# exactly this sequence — an invariant expressed three files from either route.
# Task 11 replaced the greedy parameter with `?id=`, and
# test_app_contract.py::test_no_route_uses_a_greedy_path_parameter now fails on
# any future one, so the collision is impossible rather than merely avoided.
app.include_router(entities_router, prefix=API_PREFIX, tags=["entities"])
app.include_router(data_router, prefix=API_PREFIX, tags=["entities"])
app.include_router(shapes_router, prefix=API_PREFIX, tags=["shapes"])
app.include_router(meta_router, prefix=API_PREFIX, tags=["meta"])
app.include_router(graph_router, prefix=API_PREFIX, tags=["graph"])


# ---------------------------------------------------------------------------
# Meta routes
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health():
    """Liveness and readiness check consumed by Docker and read clients.

    This route is intentionally synchronous: ``TripleStore.health()`` uses
    a synchronous ``httpx.Client``, and FastAPI correctly runs sync routes in a
    threadpool so the event loop is not blocked.

    Returns:
        A JSON object with two keys:

        - ``status``: always ``"ok"`` when the application process is alive
          (the store may still be down).
        - ``store``: ``"up"`` when the triplestore responds with HTTP < 500,
          ``"down"`` otherwise.

    No ``seed`` key, unlike curator-backend's ``/health``: this service never
    seeds, so it has no seed report to report. The store-status key is also named
    ``store`` here rather than the curator's legacy ``oxigraph`` — nothing
    consumes either one today, and naming it after the seam rather than the
    implementation is the direction the rest of the code already went.
    """
    return {
        "status": "ok",
        "store": "up" if app.state.store.health() else "down",
    }
