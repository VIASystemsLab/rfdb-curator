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
import sys

import pytest

# The top-level packages each service owns; both services use the same names,
# which is why each suite runs in its own process.
_SERVICE_PACKAGES = ("app", "api", "core", "models", "scripts")


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


def _service_modules() -> dict:
    return {n: m for n, m in sys.modules.items() if n.split(".")[0] in _SERVICE_PACKAGES}


@pytest.fixture
def isolated_service_modules():
    """Let a test re-import service modules, then put the originals back.

    Settings are built when ``core.config`` is imported, so a test that needs
    other settings drops modules from ``sys.modules`` and imports them again.
    Left in place, those fresh modules would hold a second settings object
    while modules imported earlier keep the first, and later tests would pass
    or fail depending on the order they run in. This restores every service
    module, and each submodule's attribute on its parent package, as they were.
    """
    saved = _service_modules()
    yield
    for name in list(_service_modules()):
        del sys.modules[name]
    sys.modules.update(saved)
    for name, module in saved.items():
        parent, _, child = name.rpartition(".")
        if parent and parent in saved:
            setattr(saved[parent], child, module)
