"""One version per package, across every file that names one.

The same pin is written down in four places, and each is read by a different
consumer:

* ``<service>/pyproject.toml`` — what ``uv`` resolves into the root ``uv.lock``,
  and therefore what the test suite and every local check actually import.
* ``<service>/requirements.txt`` — what ``pip install`` puts in the **Docker
  image**. The Dockerfiles do not use ``uv.lock`` at all.
* ``rfdb-core/pyproject.toml`` — the shared library's own pins, deliberately
  mirroring the services' for the five packages it uses.
* the root ``requirements.txt`` — documentation, installed by nothing.

The consequence of a divergence is asymmetric and quiet: the suite passes on one
set of versions while every container runs another, so the bump is *verified*
against software that is not *deployed*. A bump that edits the ``pyproject.toml``
files and ``uv.lock`` but misses a ``requirements.txt`` ships a refresh that
reaches no container.

Each service's ``requirements.txt`` carries a comment saying it is kept in
lockstep. A comment is a request to remember; this is the check. Same reasoning as
``test_compose_topology.py``: repo-topology assertions belong wherever CI already
runs the repo-wide checks, and they must hold with no Docker daemon and no
network.

**Deliberately not checked here:** that ``rfdb-core``'s pins mirror the services'
for the packages they share. uv already enforces it, and harder than a test could
— a one-sided bump makes the workspace unsatisfiable, so ``uv lock``, ``uv sync``
and even ``uv run`` refuse with *"Because rfdb-core depends on pydantic==2.12.5
and rfdb-curator-backend depends on pydantic==2.13.4…"*. A test asserting it would
restate the resolver rather than cover a gap, which is the distinction that earns a
test its place: the four ``requirements.txt`` checks below hold because
**nothing** reads those files during resolution.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("service", SERVICES)
def test_requirements_txt_agrees_with_pyproject(service: str) -> None:
    """The image's pins and the suite's pins must be the same versions.

    A subset is allowed in one direction only: ``requirements.txt`` carries just
    the runtime dependencies, so dev-group packages (pytest, ruff, httpx2) are
    absent from it. Every package it *does* name must match.
    """
    declared = _pyproject_pins(service)
    installed = _requirements_pins(REPO_ROOT / service / "requirements.txt")

    assert installed, f"{service}/requirements.txt declares no pins"

    missing = sorted(set(installed) - set(declared))
    assert not missing, (
        f"{service}/requirements.txt pins packages its pyproject.toml does not "
        f"declare: {missing}. The image would install something uv never resolved."
    )

    mismatched = {
        name: (installed[name], declared[name])
        for name in installed
        if installed[name] != declared[name]
    }
    assert not mismatched, (
        f"{service}: image vs. suite version drift (requirements.txt, pyproject.toml): "
        f"{mismatched}. The Docker image does not read uv.lock, so both must be bumped."
    )


@pytest.mark.parametrize("service", SERVICES)
def test_runtime_dependencies_all_reach_the_image(service: str) -> None:
    """Nothing a service imports at runtime may be missing from its image.

    The reverse of the check above, and the one that catches a *new* dependency:
    adding it to ``pyproject.toml`` makes the suite pass locally while the
    container fails at import time. ``rfdb-core`` is excluded — the Dockerfiles
    install it from source with ``pip install -e /opt/rfdb-core``, not from an
    index.
    """
    data = tomllib.loads((REPO_ROOT / service / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = _parse_pins(data["project"]["dependencies"])
    installed = _requirements_pins(REPO_ROOT / service / "requirements.txt")

    absent = sorted(set(runtime) - set(installed))
    assert not absent, (
        f"{service}/requirements.txt is missing runtime dependencies {absent}; "
        "the container would fail on import while the suite passed."
    )


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
