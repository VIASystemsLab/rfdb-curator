"""Writer-only environment for the curator suite.

These variables exist on ``curator-backend``'s ``Settings`` and nowhere else —
they configure seeding, destructive reset, and the write-permission switches. The
shared ones (store URL, graph URI, schema path, CORS) come from the root
``tests/conftest.py``.

Every value here is chosen to be inert: nothing seeds, nothing resets, nothing is
locked read-only. Tests that need the opposite set it themselves, so a test that
forgets to is a test that cannot accidentally wipe a developer's store.
"""

from __future__ import annotations

import os


def pytest_configure() -> None:
    """Provide defaults required by the curator's Settings at import time."""
    os.environ.setdefault("VOCAB_PATH", '["data/rfdb_vocab.ttl"]')
    os.environ.setdefault("DATA_PATH", "data/rfdb_data.ttl")
    os.environ.setdefault("RESET_DATA_ON_STARTUP", "false")
    os.environ.setdefault("SEED_VOCAB_ON_STARTUP", "false")
    os.environ.setdefault("SEED_TEST_DATA_ON_STARTUP", "false")
