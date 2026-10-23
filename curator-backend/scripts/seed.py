#!/usr/bin/env python3
"""Seed the triple store as a one-shot job, without starting a web server.

Same three steps the FastAPI lifespan runs — wait for the store, optionally wipe
it, load vocab (and optionally test data) — because it calls the same
``core.seeder.bootstrap_store()``. It exists so a deployment can separate
"populate the store" from "serve requests":

    docker compose run --rm curator-backend python scripts/seed.py

That matters for the read-only deploy mode (docs/deployment.md, "Deploy modes"),
where the stack runs dataexplorer-backend alone and no writer process is up to
seed on startup. Also useful for re-seeding after adding a vocabulary file,
without restarting the API.

Reads exactly the same environment variables as the service (``core.config``),
so a compose ``run`` inherits the right configuration by construction. Notably
``RESET_DATA_ON_STARTUP=true`` is honoured here too: this script will wipe the
store if the environment says to.

Idempotent: vocabulary seeding merges rather than replaces, so re-running is safe.
Exits 0 on success, 1 with the reason on failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings  # noqa: E402
from core.seeder import bootstrap_store  # noqa: E402
from rfdb_core.logging_config import configure_logging  # noqa: E402
from rfdb_core.triplestore import build_triplestore  # noqa: E402


def main() -> int:
    # Same structured logging as the service, so a one-shot run lands in the
    # same JSON-lines file as the startup seed it replaces.
    configure_logging(settings.log_file, settings.log_level)

    store = build_triplestore(settings)
    try:
        report = bootstrap_store(store, settings)
    except Exception as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
