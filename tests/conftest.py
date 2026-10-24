"""Root pytest bootstrap: the environment every service's settings need.

Only the variables in ``rfdb_core.config.BaseServiceSettings`` — the ones both
backends read — are set here. The curator's writer-only variables (seeding,
reset, write-permission switches) live in ``tests/curator/conftest.py``, because
a read-service test has no use for them and setting them here would blur which
service owns what.

``tests/core/`` needs nothing beyond these: its subjects are the schema and the
triplestore seam.

This keeps test collection independent from external CI environment wiring.
"""

from __future__ import annotations

import os


def pytest_configure() -> None:
    """Provide the defaults BaseServiceSettings requires at import time.

    Existing values from the shell/CI are preserved, so a live-stack run can
    point the suite at a real store by exporting the variable.
    """
    os.environ.setdefault("OXIGRAPH_URL", "http://localhost:7878")
    os.environ.setdefault("DATA_GRAPH_URI", "https://rfdb.it/graph/data")
    os.environ.setdefault("SCHEMA_PATH", "schema/rfdb_schema.ttl")
    # The vendored schema and seed data name their records and shapes here.
    os.environ.setdefault("DATA_BASE_URI", "https://rosfeatr.eu/rdf/data/")
    os.environ.setdefault("SCHEMA_BASE_URI", "https://rosfeatr.eu/rdf/schema/")
    os.environ.setdefault("CORS_ORIGINS", '["http://localhost:5173"]')
