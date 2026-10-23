"""The deploy-mode invariant, as a test instead of a note to remember.

Which kind of instance this is, read or full, is a deploy-time choice: base services
carry no ``profiles:`` key and run in every mode, while ``curator-backend`` and
``curator-frontend`` are ``full``-only. Compose versions **disagree** about what a
``depends_on`` pointing into an inactive profile does: some silently activate that
profile (so read mode quietly starts the editor), some hard-error (so read mode
will not start at all). Either way the failure appears at deploy time, on someone
else's machine, from an edit that looked local, so every compose edit is checked
here.

These tests read the compose files directly rather than shelling out to
``docker compose config`` — the assertions are about what the files declare, and
they should hold on a machine with no Docker daemon. ``docker compose config``
validation is a separate CI step, which catches the different class of problem
(bad interpolation, unknown keys).

Repo topology rather than library behaviour, so it sits in ``tests/core`` only
because that is the suite CI already runs the repo-wide checks alongside.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")

# The editing tier, and the only services allowed to be profile-gated. Named
# explicitly: a *new* profiled service is exactly the kind of change that should
# fail here and be looked at, rather than pass because the rule was generic.
FULL_ONLY = {"curator-backend", "curator-frontend"}


def _services(filename: str) -> dict[str, dict]:
    """Parse one compose file's service map, resolving YAML anchors."""
    parsed = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
    return parsed["services"]


def _profiles(service: dict) -> set[str]:
    return set(service.get("profiles") or ())


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_only_the_editing_tier_is_profile_gated(filename: str) -> None:
    """Exactly ``curator-backend`` and ``curator-frontend`` carry ``profiles:``.

    Read mode has to be the *default* — a plain ``docker compose up`` — or it is
    not really a mode, just a flag someone has to remember. That only works while
    every other service is unprofiled.
    """
    services = _services(filename)
    gated = {name for name, svc in services.items() if _profiles(svc)}

    assert gated == FULL_ONLY & set(services), (
        f"{filename}: profile-gated services are {sorted(gated)}, expected "
        f"{sorted(FULL_ONLY & set(services))}. A read-only instance runs everything "
        "that is not gated, so gating anything else removes it from read mode."
    )
    for name in gated:
        assert _profiles(services[name]) == {"full"}, f"{filename}: {name} has an odd profile"


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_no_dependency_crosses_into_a_profile(filename: str) -> None:
    """Every ``depends_on`` target must be a base (always-on) service.

    This is the invariant Compose versions disagree about. Keeping every target
    unprofiled sidesteps the disagreement entirely instead of relying on one
    version's behaviour: ``curator-frontend`` → ``curator-backend`` is fine
    because both are ``full``, but nothing may depend on a service that its own
    mode does not start.
    """
    services = _services(filename)
    violations = [
        f"{name} ({sorted(_profiles(svc)) or 'base'}) -> {dep} ({sorted(_profiles(services[dep]))})"
        for name, svc in services.items()
        for dep in (svc.get("depends_on") or {})
        if dep in services and _profiles(services[dep]) - _profiles(svc)
    ]

    assert not violations, (
        f"{filename}: dependency crosses into a profile the depender does not "
        f"activate: {violations}. Point it at a base service instead."
    )


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_every_dependency_target_exists(filename: str) -> None:
    """A ``depends_on`` naming a service that is not defined fails at deploy time."""
    services = _services(filename)
    missing = [
        f"{name} -> {dep}"
        for name, svc in services.items()
        for dep in (svc.get("depends_on") or {})
        if dep not in services
    ]
    assert not missing, f"{filename}: undefined dependency targets: {missing}"


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_the_reader_never_waits_on_the_writer(filename: str) -> None:
    """The reader's independence from the writer is the point of the split.

    It is also what makes read mode possible at all: a dependency here would put
    the writer back into every deployment, and would make an outage of the
    editing tier an outage of the public site.
    """
    reader = _services(filename)["dataexplorer-backend"]
    assert not FULL_ONLY & set(reader.get("depends_on") or {}), (
        f"{filename}: dataexplorer-backend depends on the write tier"
    )


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_both_backends_get_the_same_read_only_shapes(filename: str) -> None:
    """One YAML anchor gives both backends the same ``READ_ONLY_SHAPES``.

    Both services serve the same shape catalogue with the same ``readOnly``
    flags, and the editor fetches it from the reader so it can start with the
    writer down. Two literals that could be edited apart would hand clients
    different flags depending on which service answered, and no single-service
    test would notice, because each service would be internally consistent.

    The anchor makes them one value; this asserts the *resolved* values match, so
    it fails whether the anchor was replaced by a literal or simply forgotten.
    """
    services = _services(filename)
    values = {
        name: svc["environment"]["READ_ONLY_SHAPES"]
        for name, svc in services.items()
        if "READ_ONLY_SHAPES" in (svc.get("environment") or {})
    }

    assert set(values) == {"curator-backend", "dataexplorer-backend"}, (
        f"{filename}: READ_ONLY_SHAPES reaches {sorted(values)}; both backends need it"
    )
    assert len(set(values.values())) == 1, f"{filename}: the two values have drifted: {values}"


@pytest.mark.parametrize(
    "variable",
    [
        "DATA_BASE_URI",
        "SCHEMA_BASE_URI",
        "LONG_TEXT_PREDICATES",
        "EXTERNAL_ID_LINKS",
        "PROJECT_NAME",
    ],
)
@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_both_backends_get_the_same_schema_settings(filename: str, variable: str) -> None:
    """Settings both backends must agree on, or they describe the data differently.

    The writer mints under the base IRIs and the reader serves and parses them;
    both build field descriptors from the long-text list. Different values fail
    silently, with no error from either service.
    """
    services = _services(filename)
    values = {
        name: svc["environment"][variable]
        for name, svc in services.items()
        if variable in (svc.get("environment") or {})
    }

    assert set(values) == {"curator-backend", "dataexplorer-backend"}, (
        f"{filename}: {variable} reaches {sorted(values)}; both backends need it"
    )
    assert len(set(values.values())) == 1, f"{filename}: the two values have drifted: {values}"
