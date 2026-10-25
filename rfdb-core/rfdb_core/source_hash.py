"""A fingerprint of the Python sources a service runs.

A dev backend serves whichever checkout its bind mounts point at, and reloads
when that checkout changes. With ``REPORT_SOURCE_HASH`` on, ``/health`` reports
this fingerprint, computed when the service loaded its code, and the live HTTP
suite computes the same one over the checkout under test: a mismatch means the
stack runs other code (another worktree, an unfinished reload, an image built
from an older tree).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import rfdb_core

# Never part of the source: caches and environments, wherever they sit.
_SKIPPED_DIRS = frozenset({"__pycache__", ".venv", "node_modules"})


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(
            part in _SKIPPED_DIRS or part.startswith(".")
            for part in path.relative_to(root).parts[:-1]
        )
    )


def source_hash(*roots: Path) -> str:
    """SHA-256 over every ``*.py`` file under ``roots``: its relative path and its bytes.

    Roots are hashed in the order given, so the same files under different roots
    differ. Directories that are caches or environments, or hidden, are skipped.
    """
    digest = hashlib.sha256()
    for index, root in enumerate(roots):
        for path in _python_files(root):
            digest.update(f"{index}:{path.relative_to(root).as_posix()}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def service_source_hash(service_dir: Path) -> str:
    """The fingerprint of a service: its own folder, then the ``rfdb_core`` it imported."""
    return source_hash(service_dir, Path(rfdb_core.__file__).parent)
