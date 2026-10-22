#!/usr/bin/env python3
"""Reconcile object storage against RDF for the upload-first digital copies.

RDF is the source of truth; storage is garbage-collected against it:

  * **staged + referenced**  → promote to ``registered/`` (crash recovery: the
    entity write succeeded but the promotion move failed/was interrupted).
  * **staged + unreferenced, older than TTL** → delete (abandoned form).
  * **registered + unreferenced, older than grace** → delete (entity or file
    entry was removed; grace protects in-flight edits).
  * **orphaned nodes** (typed ``schema:DigitalDocument`` but no inbound
    schema-link) → purge their triples and objects.

Run it inside the curator-backend container so all service env vars are present:

    docker compose exec curator-backend python scripts/cleanup_files.py --dry-run
    docker compose exec curator-backend python scripts/cleanup_files.py

Run periodically (operator's note in TODO.md). ``GET /api/v1/dataexplorer/meta/files`` / the
Data Context Panel show the same counts for visibility between runs.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfdb_core.file_storage import (  # noqa: E402
    REGISTERED_PREFIX,
    STAGED_PREFIX,
    registered_key,
)
from rfdb_core.files_state import collect_file_state, key_file_id  # noqa: E402


class NoFileLinks(RuntimeError):
    """The schema declares no file upload, but file state exists."""


def reconcile(
    storage,
    store,
    extractor,
    *,
    data_base: str,
    staged_ttl_s: float,
    registered_grace_s: float,
    now: float,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Apply the reconciliation rules; return the actions (taken or planned).

    Pure orchestration over the injected services so tests can drive it with
    fakes and a fixed ``now``. ``data_base`` is the ``DATA_BASE_URI`` setting,
    which file nodes are named under.

    Returns:
        Dict of action lists keyed by ``promoted`` / ``deleted_staged`` /
        ``deleted_registered`` / ``purged_nodes`` (file ids).

    Raises:
        NoFileLinks: the schema declares no file upload, yet the store holds
            ``schema:DigitalDocument`` nodes or storage holds objects. Every file
            would count as unreferenced and be deleted, so nothing is done: that
            state means a schema swap or a schema mistake, not garbage.
    """
    state = collect_file_state(storage, store, extractor, data_base)
    if not extractor.file_links() and (state["typed"] or state["staged"] or state["registered"]):
        raise NoFileLinks(
            "The schema declares no file upload (no property whose sh:node shape targets "
            "schema:DigitalDocument), but file nodes or stored files exist. Refusing to "
            "delete them; fix the schema or remove the files deliberately."
        )
    linked: set[str] = state["linked"]
    actions: dict[str, list[str]] = {
        "promoted": [],
        "deleted_staged": [],
        "deleted_registered": [],
        "purged_nodes": [],
    }

    # Orphaned nodes: typed but not linked from any parent. Their objects are
    # deleted here too, so the age-based loops below must skip them (the
    # listings were snapshotted before this purge).
    purged = set()
    for file_id in sorted(state["typed"] - linked):
        purged.add(file_id)
        actions["purged_nodes"].append(file_id)
        if not dry_run:
            file_iri = f"{data_base}{file_id}"
            store.update(
                f"{store.with_clause()} "
                f"DELETE {{ <{file_iri}> ?p ?o . }} WHERE {{ <{file_iri}> ?p ?o . }}"
            )
            storage.delete(registered_key(file_id))

    for obj in state["staged"]:
        file_id = key_file_id(obj.key, STAGED_PREFIX)
        if file_id in purged:
            continue
        if file_id in linked:
            # Referenced but never promoted (crash between persist and move).
            actions["promoted"].append(file_id)
            if not dry_run:
                if storage.exists(registered_key(file_id)):
                    storage.delete(obj.key)  # registered copy already exists
                else:
                    storage.move(obj.key, registered_key(file_id))
        elif now - obj.last_modified > staged_ttl_s:
            actions["deleted_staged"].append(file_id)
            if not dry_run:
                storage.delete(obj.key)

    for obj in state["registered"]:
        file_id = key_file_id(obj.key, REGISTERED_PREFIX)
        if file_id in purged:
            continue
        if file_id not in linked and now - obj.last_modified > registered_grace_s:
            actions["deleted_registered"].append(file_id)
            if not dry_run:
                storage.delete(obj.key)

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--staged-ttl-hours",
        type=float,
        default=24,
        help="delete unreferenced staged files older than this (default: 24)",
    )
    parser.add_argument(
        "--registered-grace-hours",
        type=float,
        default=24,
        help="delete unreferenced registered files older than this (default: 24)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = parser.parse_args()

    # Build the real services from env (run inside the backend container).
    from core.config import settings
    from rfdb_core.file_storage import build_storage
    from rfdb_core.schema_extractor import SchemaExtractor
    from rfdb_core.triplestore import build_triplestore

    storage = build_storage(settings)
    store = build_triplestore(settings)
    extractor = SchemaExtractor(settings.schema_path)

    try:
        actions = reconcile(
            storage,
            store,
            extractor,
            data_base=settings.data_base_uri,
            staged_ttl_s=args.staged_ttl_hours * 3600,
            registered_grace_s=args.registered_grace_hours * 3600,
            now=time.time(),
            dry_run=args.dry_run,
        )
    except NoFileLinks as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 1

    prefix = "[dry-run] would " if args.dry_run else ""
    for action, ids in actions.items():
        label = action.replace("_", " ")
        print(f"{prefix}{label}: {len(ids)}" + (f" — {', '.join(ids)}" if ids else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
