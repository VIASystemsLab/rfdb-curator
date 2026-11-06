"""The root ``requirements.txt`` must name versions the workspace really uses.

Python pins live in each member's ``pyproject.toml`` and are locked in the root
``uv.lock``, which the suite, every local check and the Docker images all install
from, so uv itself keeps those consistent. The one copy uv never reads is the root
``requirements.txt``: documentation, installed by nothing, and so free to go stale
unnoticed. This is its check. Same reasoning as ``test_compose_topology.py``:
repo-topology assertions belong wherever CI already runs the repo-wide checks, and
they must hold with no Docker daemon and no network.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("curator-backend", "dataexplorer-backend")

# Installed by nothing — it exists to tell a reader what the project centres on —
# but a stale version here is a misleading answer to "what does this run?", which
# is the only question it is there to answer.
DOC_REQUIREMENTS = "requirements.txt"


def _parse_pins(lines: list[str]) -> dict[str, str]:
    """Map ``name -> version`` from ``==`` requirement lines, extras stripped.

    ``uvicorn[standard]==0.52.1`` and ``uvicorn==0.52.1`` are the same pin for
    this purpose: the extras select optional dependencies, not a version.
    """
    pins: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pins[name.split("[")[0].strip().lower()] = version.strip()
    return pins


def _requirements_pins(path: Path) -> dict[str, str]:
    return _parse_pins(path.read_text(encoding="utf-8").splitlines())


def _pyproject_pins(directory: str) -> dict[str, str]:
    """Every pin a member declares — runtime, optional extras and dev groups.

    Flattened together on purpose. The workspace resolves one version per package
    across all members and all groups, so ``pytest`` in a dev group and ``rdflib``
    in a runtime list are subject to the same single-version rule.
    """
    data = tomllib.loads((REPO_ROOT / directory / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    lines = list(project.get("dependencies", []))
    for extra in (project.get("optional-dependencies") or {}).values():
        lines.extend(extra)
    for group in (data.get("dependency-groups") or {}).values():
        lines.extend(group)
    return _parse_pins(lines)


def test_documentation_requirements_are_not_stale() -> None:
    """The root ``requirements.txt`` documents versions, so they must be real ones.

    Installed by nothing, which is precisely why it goes stale unnoticed. Checked
    against whichever member declares each package.
    """
    documented = _requirements_pins(REPO_ROOT / DOC_REQUIREMENTS)
    assert documented, f"{DOC_REQUIREMENTS} declares no pins"

    actual: dict[str, str] = {}
    for directory in ("rfdb-core", *SERVICES):
        actual.update(_pyproject_pins(directory))

    unknown = sorted(set(documented) - set(actual))
    assert not unknown, f"{DOC_REQUIREMENTS} names packages no member declares: {unknown}"

    mismatched = {
        name: (documented[name], actual[name])
        for name in documented
        if documented[name] != actual[name]
    }
    assert not mismatched, (
        f"{DOC_REQUIREMENTS} is stale (documented, actual): {mismatched}. "
        "It is installed by nothing, so nothing else would have caught this."
    )
