"""FastAPI application entrypoint for the RossijskijFeatrDB data-entry backend.

Startup sequence (lifespan context manager):
  1. Parse ``schema/schema.ttl`` into a ``SchemaExtractor`` and a
     ``ShaclValidator`` (both read the same file; see note in lifespan).
  2. Build a ``TripleStore`` via ``build_triplestore(settings)`` (Oxigraph today).
  3. Optionally call ``TripleStore.clear_store()`` when ``reset_data_on_startup``
     is ``true`` — wipes all named graphs and the default graph before seeding.
     If this step fails the process aborts; the app must not start with a
     partially-cleared store.
  4. Call ``seed_store()`` to load ``vocab.ttl`` (and optionally ``data.ttl``) into
     Oxigraph.  When ``reset_data_on_startup`` is ``false`` this is idempotent;
     duplicate triples are silently merged by Oxigraph.

The schema extractor, shape dependency graph, SHACL validator, triplestore,
object storage and seed report are stored on ``app.state`` so every route
handler can access them via ``request.app.state.<name>`` without global imports.

Note — startup blocking I/O:
    All startup work (file parsing, HTTP calls to Oxigraph) is synchronous and
    runs on the event loop thread inside the lifespan context manager.  This is
    safe because no requests are served until after ``yield``.  If startup time
    becomes a concern, wrap each blocking call in ``asyncio.to_thread()``.

Note — Oxigraph readiness:
    The Oxigraph Docker healthcheck (``oxigraph --help``) only proves the
    process is alive, not that its HTTP port is accepting connections yet.
    Steps 3 and 4 are therefore delegated to ``core.seeder.bootstrap_store()``,
    which polls ``TripleStore.health()`` before any reset/seed call that would
    otherwise raise on a refused connection. ``scripts/seed.py`` calls the same
    function, so a one-shot seed job cannot skip the poll.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from rdflib.plugins.parsers.notation3 import BadSyntax

from api.data import router as data_router
from api.files import router as files_router
from api.shapes import router as shapes_router
from api.validate import router as validate_router
from core.config import settings
from core.seeder import bootstrap_store
from core.shacl_validator import ShaclValidator
from core.validation_merge import _build_shape_dep_graph
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
    """Initialise shared services before the server starts accepting requests.

    Follows the FastAPI lifespan protocol: everything before ``yield`` runs at
    startup; everything after ``yield`` runs at shutdown (nothing to tear down
    here because Oxigraph runs as a separate container).

    All work is synchronous and blocking — see the module-level note on startup
    blocking I/O for the rationale and a migration path if needed.

    Steps
    -----
    1. Build the in-memory SHACL index (``SchemaExtractor``) and validator
       (``ShaclValidator``).  Both parse ``settings.schema_path`` independently;
       this is a known redundancy that can be eliminated by passing a pre-parsed
       rdflib ``Graph`` to ``ShaclValidator`` once the interface supports it.
    2. Build the ``TripleStore`` and the object-storage client.
    3. Hand off to ``bootstrap_store()``, which polls the store until it
       responds, wipes it when ``reset_data_on_startup`` is ``True`` (any
       exception there propagates and aborts startup intentionally — the app
       must not serve requests against a partially-cleared store), and seeds
       vocab plus optional test data.
    4. Store the returned seed report on ``app.state`` for ``/health``.
    """
    logger.info("Starting up RossijskijFeatrDB backend…")

    # -- 1. Schema services ------------------------------------------------
    try:
        app.state.schema_extractor = SchemaExtractor(
            settings.schema_path, settings.long_text_predicates
        )
        app.state.shape_dep_graph = _build_shape_dep_graph(app.state.schema_extractor)
        app.state.shacl_validator = ShaclValidator(settings.schema_path)
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

    # -- 2. Triplestore ----------------------------------------------------
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

    # -- 3. Readiness poll + optional reset + seed --------------------------
    # Shared with `python scripts/seed.py`, which runs the same three steps as a
    # one-shot job. Keeping them in one function is what stops a deployment that
    # seeds out-of-band from skipping the readiness poll or the reset guard.
    app.state.seed_report = bootstrap_store(app.state.store, settings)

    logger.info("Startup complete — accepting requests.")
    yield
    # Shutdown: nothing to tear down (Oxigraph runs as a separate container).


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RossijskijFeatrDB Data Entry API",
    description="SHACL-driven data entry backend for the Russian Theatre DB.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS guard + middleware, the StorageError -> 503 handler, and the api.access
# request log — all shared with dataexplorer-backend so the two services behave
# identically at the edges. See rfdb_core.app_factory for the middleware-order
# note; it is load-bearing.
configure_app(app, cors_origins=settings.cors_origins)

# Writer surface only, under a prefix that names its owner (D8). Entity search,
# graph neighbourhoods and the meta routes are reads served by
# dataexplorer-backend; the shapes route is served here *and* there, now with an
# identical payload from one shared implementation (D11).
#
# Naming the service in the path is what makes wrong-service requests legible and
# read-only deployments trivial — the whole namespace is simply absent rather than
# individual routes returning 403. It costs nothing in public-contract terms
# because the durable, third-party-facing identifiers live in the reader's
# unversioned /rdf/ space, not here.
#
# This service publishes nothing under /rdf/: it is the writer, and a published
# identifier should not resolve to the tier that happens to mint it.
API_PREFIX = "/api/v1/curator"

app.include_router(shapes_router, prefix=API_PREFIX, tags=["shapes"])
app.include_router(data_router, prefix=API_PREFIX, tags=["entities"])
app.include_router(files_router, prefix=API_PREFIX, tags=["files"])
app.include_router(validate_router, prefix=API_PREFIX, tags=["validation"])


# ---------------------------------------------------------------------------
# Meta routes
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health():
    """Liveness and readiness check consumed by Docker and the frontend status bar.

    This route is intentionally synchronous: ``TripleStore.health()`` uses
    a synchronous ``httpx.Client``, and FastAPI correctly runs sync routes in a
    threadpool so the event loop is not blocked.

    Returns:
        A JSON object with three keys:

        - ``status``: always ``"ok"`` when the application process is alive
          (Oxigraph may still be down).
        - ``oxigraph``: ``"up"`` when Oxigraph responds with HTTP < 500,
          ``"down"`` otherwise.
        - ``seed``: the seed report dict produced during startup, or ``null``
          if startup has not yet completed (should not occur in normal operation).
    """
    ox_ok = app.state.store.health()
    return {
        "status": "ok",
        "oxigraph": "up" if ox_ok else "down",
        "seed": getattr(app.state, "seed_report", None),
    }
