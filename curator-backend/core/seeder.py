"""Data seeder for the triple store — the curator's, and only the curator's.

Two entry points, one code path:

  * ``bootstrap_store()`` is called by the FastAPI lifespan on every startup;
  * ``scripts/seed.py`` calls the same function as a one-shot job, so a
    deployment can seed without starting a web server (see the read-only
    deploy mode in docs/deployment.md, "Deploy modes").

Design notes:
  - vocabulary seeding is idempotent: Oxigraph merges triples rather than replacing,
    so re-seeding on every restart is safe.
  - the sample data is for development and testing only; it is never seeded in
    production unless explicitly enabled via `SEED_TEST_DATA_ON_STARTUP=true`.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Bounds for the store HTTP-readiness poll in wait_for_store().
# Oxigraph's own storage init (RocksDB) can take well over a minute on a cold
# start with an existing/large data volume, long after Docker's liveness
# healthcheck (`oxigraph --help`, which never touches storage) reports healthy.
_READY_TIMEOUT_S = 120.0
_READY_POLL_S = 0.5


def _seed_turtle_file(store, file_path: str) -> dict:
    """Load a single Turtle file into Oxigraph and return a status dict.

    Returns ``{'path': ..., 'loaded': False, 'reason': 'missing'}`` when the
    file does not exist rather than raising, so the startup report remains
    informative even in environments where some files are absent.
    """
    path = Path(file_path)
    if not path.exists():
        return {"path": str(path), "loaded": False, "reason": "missing"}

    turtle_data = path.read_text(encoding="utf-8")
    store.load_turtle(turtle_data)
    return {"path": str(path), "loaded": True}


def seed_store(
    store,
    vocab_paths: list[str],
    test_data_path: str,
    seed_vocab: bool,
    seed_test_data: bool,
) -> dict:
    """Seed Oxigraph on startup.

    Policy:
    - vocab_paths are canonical and should be seeded by default (idempotent).
    - test_data_path is test fixture and is optional/off by default.
    """
    report = {
        "seedVocab": bool(seed_vocab),
        "seedTestData": bool(seed_test_data),
        "results": [],
    }

    if seed_vocab:
        for path in vocab_paths:
            report["results"].append(_seed_turtle_file(store, path))

    if seed_test_data:
        report["results"].append(_seed_turtle_file(store, test_data_path))

    return report


def wait_for_store(store, url: str) -> None:
    """Block until the store answers ``health()``, or abort.

    Docker's ``depends_on: condition: service_healthy`` only proves the Oxigraph
    *process* is alive — its healthcheck runs ``oxigraph --help``, which never
    touches the network. It does not prove the HTTP port is accepting
    connections. Without this poll a fresh ``compose up`` loses the startup race
    and dies on a refused connection.

    Needed by both entry points: the one-shot seed job hits exactly the same
    cold-start race as the web process.

    Raises:
        RuntimeError: the store did not answer within ``_READY_TIMEOUT_S``.
    """
    deadline = time.monotonic() + _READY_TIMEOUT_S
    while not store.health():
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Triplestore not reachable at '{url}' after {_READY_TIMEOUT_S}s "
                "— aborting startup."
            )
        time.sleep(_READY_POLL_S)
    logger.info("Triplestore is reachable.")


def bootstrap_store(store, settings) -> dict:
    """Bring the store to its baseline state: wait, optionally wipe, then seed.

    The whole of what a deployment needs to do to a store before it is usable,
    in one call, so the lifespan and the one-shot ``scripts/seed.py`` cannot
    drift apart.

    Args:
        store: A ``TripleStore``.
        settings: Anything exposing ``oxigraph_url``, ``reset_data_on_startup``,
            ``vocab_paths``, ``data_path``, ``seed_vocab_on_startup`` and
            ``seed_test_data_on_startup`` — i.e. the curator ``Settings``.

    Returns:
        The seed report, as served by ``/health``'s ``seed`` key.

    Raises:
        RuntimeError: the store never became reachable.
        Exception: anything ``clear_store()`` raises propagates deliberately —
            the app must not serve requests against a partially-cleared store.
    """
    wait_for_store(store, str(settings.oxigraph_url))

    if settings.reset_data_on_startup:
        logger.warning(
            "reset_data_on_startup=true — clearing all triples from the store. "
            "This is a destructive operation; do NOT enable in production."
        )
        store.clear_store()
        logger.warning("Store cleared successfully.")

    report = seed_store(
        store=store,
        vocab_paths=settings.vocab_paths,
        test_data_path=settings.data_path,
        seed_vocab=settings.seed_vocab_on_startup,
        seed_test_data=settings.seed_test_data_on_startup,
    )
    logger.info("Seed complete: %s", report)
    return report
