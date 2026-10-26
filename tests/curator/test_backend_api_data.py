"""HTTP integration coverage for the data routes, across both services.

The canonical live HTTP integration suite: it exercises the running stack over
real HTTP, including request parsing, route wiring, write/read logic and Oxigraph
persistence.

**It spans two services.** Writes go to curator-backend
(``RFDB_API_BASE_URL``, :8000) and the read-back verification goes to
dataexplorer-backend (``RFDB_READ_API_BASE_URL``, :8001). That is the strongest
thing the suite checks: a record written through the writer must be readable
through the reader, over the same store, with no coordination between the two
beyond that store.

It lives under ``tests/curator/`` because writes drive every scenario. It skips
when no stack answers ``/health``, and fails when one answers but does not settle
(see ``api_client``), or when it runs code other than this checkout's: both
backends must report ``source`` on ``/health`` (``REPORT_SOURCE_HASH``, on in the
dev compose file) equal to the checkout's hash. It writes into the stack's data
graph and does not clean up: run it against a dev stack whose store may be wiped
afterwards.

The payloads follow the rfdb shapes (the LRMoo chain, AgentRole, Contributor), so
the suite runs only against a stack serving them, and skips otherwise. The
namespaces are the stack's own: records are expected under its ``DATA_BASE_URI``
and shapes looked up under its ``SCHEMA_BASE_URI``, both read from ``meta/schema``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import pytest

from rfdb_core.source_hash import source_hash
from rfdb_core.vocab import (
    SCHEMA_CONTENT_SIZE,
    SCHEMA_CONTENT_URL,
    SCHEMA_DIGITAL_DOCUMENT,
    SCHEMA_ENCODING_FORMAT,
    SCHEMA_NAME,
    SCHEMA_SHA256,
)

# Writes -> curator-backend; reads -> dataexplorer-backend. Override either to
# point the suite at a deployed stack.
API_BASE_URL = os.getenv("RFDB_API_BASE_URL", "http://localhost:8000")
READ_API_BASE_URL = os.getenv("RFDB_READ_API_BASE_URL", "http://localhost:8001")

# Route constants, so a path change is one edit and no line wraps.
WRITE_ENTITIES = "/api/v1/curator/entities"
READ_ENTITY = "/api/v1/dataexplorer/entities/get"
META_SCHEMA = "/api/v1/dataexplorer/meta/schema"
READ_SHAPES = "/api/v1/dataexplorer/shapes"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LRMOO_F1_WORK = "http://iflastandards.info/ns/lrm/lrmoo/F1_Work"
LRMOO_F2_EXPRESSION = "http://iflastandards.info/ns/lrm/lrmoo/F2_Expression"
LRMOO_F3_MANIFESTATION = "http://iflastandards.info/ns/lrm/lrmoo/F3_Manifestation"
LRMOO_R4_EMBODIES = "http://iflastandards.info/ns/lrm/lrmoo/R4_embodies"
MM_MUSIC_ENTITY = "https://w3id.org/polifonia/ontology/music-meta/MusicEntity"
CORE_AGENT_ROLE = "https://w3id.org/polifonia/ontology/core/AgentRole"
CORE_HAS_AGENT = "https://w3id.org/polifonia/ontology/core/hasAgent"
CORE_HAS_AGENT_ROLE = "https://w3id.org/polifonia/ontology/core/hasAgentRole"
CORE_HAS_ROLE = "https://w3id.org/polifonia/ontology/core/hasRole"
P148I_IS_COMPONENT_OF = "http://www.cidoc-crm.org/cidoc-crm/P148i_is_component_of"
CORE_PERSON = "https://w3id.org/polifonia/ontology/core/Person"
CORE_ROLE = "https://w3id.org/polifonia/ontology/core/Role"
FOAF_AGENT = "http://xmlns.com/foaf/0.1/Agent"
FOAF_PERSON = "http://xmlns.com/foaf/0.1/Person"
FOAF_ORGANIZATION = "http://xmlns.com/foaf/0.1/Organization"
FOAF_NAME = "http://xmlns.com/foaf/0.1/name"

ROOT = Path(__file__).resolve().parents[2]

# The stack's DATA_BASE_URI and SCHEMA_BASE_URI, read from meta/schema by
# ``api_client`` before any test runs.
_BASES: dict[str, str] = {}


def _data_iri(local: str) -> str:
    """An IRI in the stack's record namespace."""
    return f"{_BASES['data']}{local}"


def _shape_iri(name: str) -> str:
    """A shape's IRI in the stack's schema namespace."""
    return f"{_BASES['schema']}{name}"


def _entity_url(iri: str) -> str:
    """Read-service URL for one entity.

    The IRI travels as ``?id=``, never as a path segment — see the route's docstring.
    Kept as a helper so the encoding is done once and correctly.
    """
    return f"{READ_ENTITY}?{urlencode({'id': iri})}"


def _make_suffix() -> str:
    return uuid.uuid4().hex[:8]


def _request_json(
    method: str, path: str, payload: dict | None = None, *, base: str = API_BASE_URL
) -> tuple[int, dict]:
    url = f"{base}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = UrlRequest(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}
    except URLError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"Unable to reach {url}: {exc}") from exc


def _request_text(method: str, path: str, *, base: str = API_BASE_URL) -> tuple[int, str]:
    url = f"{base}{path}"
    request = UrlRequest(url, method=method)

    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    except URLError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"Unable to reach {url}: {exc}") from exc


def _read_json(path: str) -> tuple[int, dict]:
    """GET from the READ service, dataexplorer-backend."""
    return _request_json("GET", path, base=READ_API_BASE_URL)


def _read_text(path: str) -> tuple[int, str]:
    """GET from the READ service, unparsed."""
    return _request_text("GET", path, base=READ_API_BASE_URL)


# The dev stack runs both backends with ``uvicorn --reload`` over the source
# tree, so an edit or a branch switch restarts them. One /health answer does not
# show the stack is settled: it is polled until it answers healthy several times
# in a row.
_PROBE_TRIES = 30
_PROBE_INTERVAL_S = 1.0
_PROBE_STABLE = 3
_PROBE_TIMEOUT_S = 5


def _probe_once() -> tuple[bool, bool, str]:
    """One /health round on both services: ``(reachable, healthy, detail)``.

    Reachable means both services answered at all; healthy means both report
    their store as up.
    """
    answers = []
    healthy = True
    for name, base, key in (
        ("curator-backend", API_BASE_URL, "oxigraph"),
        ("dataexplorer-backend", READ_API_BASE_URL, "store"),
    ):
        try:
            with urlopen(f"{base}/health", timeout=_PROBE_TIMEOUT_S) as response:
                status, body = response.status, response.read()
        except HTTPError as exc:
            status, body = exc.code, b""
        except (URLError, OSError) as exc:
            return False, False, f"{name} at {base}: {exc}"
        try:
            health = json.loads(body or b"{}")
        except ValueError:
            health = {}
        ok = status == 200 and health.get(key) == "up"
        healthy = healthy and ok
        answers.append(f"{name} {status} {key}={health.get(key)!r}")
    return True, healthy, "; ".join(answers)


def _check_served_code() -> None:
    """Fail unless both backends run the Python code of this checkout.

    Each backend hashes the code it loaded (``rfdb_core.source_hash``); the same
    hash over this checkout must match, or the run would test other code: a
    stack started from another worktree, a reload not finished, a stale image.
    """
    core = ROOT / "rfdb-core" / "rfdb_core"
    problems = []
    for service, base in (
        ("curator-backend", API_BASE_URL),
        ("dataexplorer-backend", READ_API_BASE_URL),
    ):
        _, health = _request_json("GET", "/health", base=base)
        served = health.get("source")
        expected = source_hash(ROOT / service, core)
        if served is None:
            problems.append(f"{service} does not report its source: set REPORT_SOURCE_HASH=true")
        elif served != expected:
            problems.append(
                f"{service} runs other code: {served[:12]} served, {expected[:12]} here"
            )
    if problems:
        pytest.fail(
            "the stack does not run this checkout:\n"
            + "\n".join(problems)
            + "\nStart it from this checkout, or let its reload finish, and run again."
        )


@pytest.fixture(scope="session")
def api_client() -> Iterator[None]:
    """Skip when no stack is running; fail when one runs but does not settle.

    Both services, because every scenario writes through one and reads back
    through the other. The two /health payloads differ by design: the writer
    reports its store under "oxigraph", the reader under "store".

    A first probe that reaches nothing means no stack, and the module skips. Once
    the stack answers, it must report healthy ``_PROBE_STABLE`` times in a row
    within ``_PROBE_TRIES`` probes; otherwise the module fails with every answer,
    so a reloading stack can pass neither as green nor as skipped.
    """
    reachable, _, detail = _probe_once()
    if not reachable:
        pytest.skip(f"live stack not running: {detail}")

    log: list[str] = []
    streak = 0
    for attempt in range(1, _PROBE_TRIES + 1):
        _, healthy, detail = _probe_once()
        log.append(f"try {attempt}: {detail}")
        streak = streak + 1 if healthy else 0
        if streak >= _PROBE_STABLE:
            break
        time.sleep(_PROBE_INTERVAL_S)
    else:
        pytest.fail(
            f"stack did not answer healthy {_PROBE_STABLE} times in a row "
            f"in {_PROBE_TRIES} tries:\n" + "\n".join(log)
        )

    _check_served_code()

    status, info = _read_json(META_SCHEMA)
    assert status == 200, info
    _BASES.update(data=info["dataBaseUri"], schema=info["schemaBaseUri"])

    status, shapes = _read_json(READ_SHAPES)
    assert status == 200, shapes
    served = {shape["id"]: shape.get("targetClassUri") for shape in shapes}
    missing = [
        name
        for name, target_class in _rfdb_shapes().items()
        if served.get(_shape_iri(name)) != target_class
    ]
    if missing:
        pytest.skip(
            "the stack does not serve the rfdb shapes these payloads follow "
            f"(missing or retargeted under {_BASES['schema']}: {', '.join(missing)})"
        )

    yield None


# ---------------------------------------------------------------------------
# Payload factories
#
# The backend names every new record and card, so a payload never carries an
# ``@id`` on create, and a record is cited by the IRI the save that created it
# returned. A payload describes one record and its cards: everything it cites
# is created first, by its own save. Each "invalid" scenario starts from a valid
# payload and removes exactly what violates one constraint, so a negative test
# traces back to a single expected cause.
# ---------------------------------------------------------------------------

SOURCE_TYPES = [
    "https://w3id.org/polifonia/ontology/source/Source",
    "http://iflastandards.info/ns/lrm/lrmoo/F5_Item",
]
CORE_TYPE = "https://w3id.org/polifonia/ontology/core/Type"
CORE_ORGANIZATION = "https://w3id.org/polifonia/ontology/core/Organization"
CORE_PLACE = "https://w3id.org/polifonia/ontology/core/Place"
CORE_HAS_PLACE = "https://w3id.org/polifonia/ontology/core/hasPlace"
CORE_HAS_TYPE = "https://w3id.org/polifonia/ontology/core/hasType"
PRISM_PUBLICATION_DATE = "http://prismstandard.org/namespaces/basic/2.0/publicationDate"
LRMOO_R7_EXEMPLIFIES = "http://iflastandards.info/ns/lrm/lrmoo/R7_exemplifies"
P51_OWNER = "http://www.cidoc-crm.org/cidoc-crm/P51_has_former_or_current_owner"


def _rfdb_shapes() -> dict[str, str]:
    """The shapes the suite writes, by local name, with the class each must target."""
    return {
        "PersonShape": CORE_PERSON,
        "RoleShape": CORE_ROLE,
        "AgentRoleShape": CORE_AGENT_ROLE,
        "MusicalWorkShape": MM_MUSIC_ENTITY,
        "ExpressionShape": LRMOO_F2_EXPRESSION,
        "ManifestationShape": LRMOO_F3_MANIFESTATION,
        "SourceShape": SOURCE_TYPES[0],
        "SourceTypeShape": CORE_TYPE,
        "PlaceShape": CORE_PLACE,
        "HoldingOrganizationShape": CORE_ORGANIZATION,
        "DigitalCopyShape": SCHEMA_DIGITAL_DOCUMENT,
        "ContributorShape": FOAF_AGENT,
    }


def _label(text: str) -> dict:
    return {"@value": text, "@language": "en"}


def _save(shape: str, data: dict, original: list[dict] | None = None) -> tuple[int, dict]:
    payload = {"shapeId": _shape_iri(shape), "data": data}
    if original is not None:
        payload["originalTriples"] = original
    return _request_json("POST", WRITE_ENTITIES, payload)


def _create(shape: str, data: dict) -> str:
    """Save a new record, check it was stored, return the IRI the backend minted."""
    assert "@id" not in data
    status, body = _save(shape, data)
    assert status == 200, body
    assert body["success"] is True, body.get("validationReport")
    return body["entityId"]


def _delete(iri: str) -> tuple[int, dict]:
    return _request_json("DELETE", f"{WRITE_ENTITIES}?{urlencode({'id': iri})}")


def _card(person_id: str, role_id: str) -> dict:
    """A new AgentRole card: no ``@id``, the backend names it."""
    return {
        "@type": CORE_AGENT_ROLE,
        CORE_HAS_AGENT: {"@id": person_id},
        CORE_HAS_ROLE: {"@id": role_id},
    }


def _work_data(label: str, *cards: dict) -> dict:
    data = {"@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK], RDFS_LABEL: _label(label)}
    if cards:
        data[CORE_HAS_AGENT_ROLE] = list(cards)
    return data


def _person_and_role(prefix: str) -> tuple[str, str]:
    person = _create("PersonShape", {"@type": CORE_PERSON, RDFS_LABEL: _label(f"{prefix} Person")})
    role = _create("RoleShape", {"@type": CORE_ROLE, RDFS_LABEL: _label(f"{prefix} Role")})
    return person, role


def _work_chain(prefix: str) -> dict[str, str]:
    """Person, Role, a Work with one card, and an Expression of it; their IRIs."""
    person, role = _person_and_role(prefix)
    work = _create("MusicalWorkShape", _work_data(f"{prefix} Work", _card(person, role)))
    expression = _create(
        "ExpressionShape",
        {
            "@type": LRMOO_F2_EXPRESSION,
            RDFS_LABEL: _label(f"{prefix} Expression"),
            P148I_IS_COMPONENT_OF: {"@id": work},
        },
    )
    return {"person": person, "role": role, "work": work, "expression": expression}


def _manifestation_data(label: str, expression_id: str) -> dict:
    return {
        "@type": LRMOO_F3_MANIFESTATION,
        RDFS_LABEL: _label(label),
        LRMOO_R4_EMBODIES: {"@id": expression_id},
    }


def _source_data(prefix: str, manifestation_id: str) -> dict:
    """A Source with publication metadata, citing a new type, owner and place."""
    source_type = _create("SourceTypeShape", {"@type": CORE_TYPE, RDFS_LABEL: _label("Libretto")})
    place = _create("PlaceShape", {"@type": CORE_PLACE, RDFS_LABEL: _label(f"{prefix} City")})
    owner = _create(
        "HoldingOrganizationShape",
        {
            "@type": CORE_ORGANIZATION,
            RDFS_LABEL: _label(f"{prefix} Library"),
            CORE_HAS_PLACE: {"@id": place},
        },
    )
    return {
        "@type": SOURCE_TYPES,
        RDFS_LABEL: _label(f"{prefix} Source"),
        "http://www.w3.org/2000/01/rdf-schema#seeAlso": {"@id": "https://example.org/catalog/1"},
        PRISM_PUBLICATION_DATE: {
            "@value": "1736",
            "@type": "http://www.w3.org/2001/XMLSchema#gYear",
        },
        CORE_HAS_TYPE: {"@id": source_type},
        "http://purl.org/dc/terms/identifier": f"Shelfmark-{prefix}",
        P51_OWNER: {"@id": owner},
        LRMOO_R7_EXEMPLIFIES: {"@id": manifestation_id},
    }


def _triples(entity_id: str) -> list[dict]:
    status, body = _read_json(_entity_url(entity_id))
    assert status == 200, entity_id
    return body["triples"]


def _objects(entity_id: str, predicate: str) -> set[str]:
    return {t["object"] for t in _triples(entity_id) if t["predicate"] == predicate}


def _assert_entity_has_label_and_type(entity_id: str, expected_type: str, expected_label: str):
    triples = _triples(entity_id)
    assert any(t["predicate"] == RDFS_LABEL and t["object"] == expected_label for t in triples)
    assert any(t["predicate"] == RDF_TYPE and t["object"] == expected_type for t in triples)
    return triples


def _assert_not_stored(entity_id: str) -> None:
    status, _ = _read_text(_entity_url(entity_id))
    assert status == 404, f"{entity_id} was stored"


def _assert_minted(iri: str, name: str) -> None:
    assert re.fullmatch(rf"{re.escape(_BASES['data'])}{name}_[0-9a-f]{{12}}", iri), iri


# ---------------------------------------------------------------------------
# Positive insert tests
# ---------------------------------------------------------------------------


def test_positive_manifestation_insert_persists(api_client):
    """A Manifestation citing a stored Expression is validated against it and stored.

    The payload gives only the Expression's ``@id``. For the Manifestation to
    validate against ExpressionShape's constraints, the backend must merge the
    Expression's stored triples into the validation graph.
    """
    chain = _work_chain(f"Manifestation {_make_suffix()}")
    status, body = _save(
        "ManifestationShape", _manifestation_data("Test Manifestation", chain["expression"])
    )
    assert status == 200
    assert body["success"] is True
    assert body["validationReport"]["conforms"] is True
    _assert_minted(body["entityId"], "Manifestation")

    triples = _assert_entity_has_label_and_type(
        body["entityId"], LRMOO_F3_MANIFESTATION, "Test Manifestation"
    )
    assert any(t["predicate"] == LRMOO_R4_EMBODIES for t in triples)


def test_positive_work_insert_persists(api_client):
    """A Work and its card are stored under IRIs the backend minted."""
    person, role = _person_and_role(f"Work {_make_suffix()}")
    work = _create("MusicalWorkShape", _work_data("Standalone Work", _card(person, role)))
    _assert_minted(work, "MusicalWork")
    _assert_entity_has_label_and_type(work, LRMOO_F1_WORK, "Standalone Work")

    (card,) = _objects(work, CORE_HAS_AGENT_ROLE)
    _assert_minted(card, "AgentRole")
    assert _objects(card, CORE_HAS_AGENT) == {person}
    assert _objects(card, CORE_HAS_ROLE) == {role}


def test_full_stack_valid_insert_sequence(api_client):
    """Work -> Expression -> Manifestation -> Source, each its own save, stay valid."""
    prefix = f"Chain {_make_suffix()}"
    chain = _work_chain(prefix)
    _assert_entity_has_label_and_type(chain["work"], LRMOO_F1_WORK, f"{prefix} Work")
    _assert_entity_has_label_and_type(
        chain["expression"], LRMOO_F2_EXPRESSION, f"{prefix} Expression"
    )
    manifestation = _create(
        "ManifestationShape", _manifestation_data(f"{prefix} Manifestation", chain["expression"])
    )
    source = _create("SourceShape", _source_data(prefix, manifestation))

    triples = _assert_entity_has_label_and_type(source, SOURCE_TYPES[0], f"{prefix} Source")
    assert any(t["predicate"] == LRMOO_R7_EXEMPLIFIES for t in triples)
    assert any(t["predicate"] == PRISM_PUBLICATION_DATE for t in triples)


# ---------------------------------------------------------------------------
# Negative insert tests
# ---------------------------------------------------------------------------


def test_negative_agent_role_card_is_rejected(api_client):
    """A card without its person and role fails AgentRoleShape; nothing is stored."""
    card = {"@type": CORE_AGENT_ROLE}
    status, body = _save("MusicalWorkShape", _work_data("Work With Empty Card", card))
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False
    assert body["validationReport"]["violations"]
    _assert_not_stored(body["entityId"])


def test_negative_work_insert_without_label_is_rejected(api_client):
    """Work missing required label fails SHACL validation and is not stored."""
    data = _work_data("unused")
    del data[RDFS_LABEL]
    status, body = _save("MusicalWorkShape", data)
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False
    assert any(
        v.get("focusNode") == body["entityId"] and v.get("path") == RDFS_LABEL
        for v in body["validationReport"]["violations"]
    )
    _assert_not_stored(body["entityId"])


def test_payload_describing_a_cited_record_is_refused(api_client):
    """A save describes its own record only: an inline Expression is a 422.

    Accepted, the Manifestation's save would write the Expression's triples
    without validating it as an Expression in its own right.
    """
    suffix = _make_suffix()
    inline_expression = _data_iri(f"inline_expression_{suffix}")
    data = _manifestation_data("Cascade Manifestation", inline_expression)
    data[LRMOO_R4_EMBODIES].update(
        {"@type": LRMOO_F2_EXPRESSION, RDFS_LABEL: _label("Inline Expression")}
    )
    status, body = _save("ManifestationShape", data)
    assert status == 422, body
    _assert_not_stored(inline_expression)


def test_create_with_an_id_is_refused(api_client):
    """``@id`` means update: a record that does not exist is a 404."""
    iri = _data_iri(f"chosen_by_the_client_{_make_suffix()}")
    status, _ = _save("MusicalWorkShape", {"@id": iri, **_work_data("Chosen IRI")})
    assert status == 404
    _assert_not_stored(iri)


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------


def _created_manifestation() -> tuple[str, dict]:
    chain = _work_chain(f"Update {_make_suffix()}")
    data = _manifestation_data("Test Manifestation", chain["expression"])
    return _create("ManifestationShape", data), data


def test_failed_update_keeps_existing_entity(api_client):
    """Failed update leaves previously persisted entity triples unchanged."""
    entity_id, data = _created_manifestation()
    invalid = {"@id": entity_id, **data}
    del invalid[RDFS_LABEL]
    original = [
        {
            "predicate": RDFS_LABEL,
            "object": "Test Manifestation",
            "objectType": "literal",
            "language": "en",
        }
    ]
    status, body = _save("ManifestationShape", invalid, original)
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False

    triples = _triples(entity_id)
    assert any(
        t["predicate"] == RDFS_LABEL and t["object"] == "Test Manifestation" for t in triples
    )
    assert any(t["predicate"] == LRMOO_R4_EMBODIES for t in triples)


def test_valid_update_replaces_label_without_dropping_type(api_client):
    """Valid update replaces label while preserving rdf:type and other structure."""
    entity_id, data = _created_manifestation()
    updated = {"@id": entity_id, **data, RDFS_LABEL: _label("Test Manifestation Updated")}
    original = [
        {
            "predicate": RDFS_LABEL,
            "object": "Test Manifestation",
            "objectType": "literal",
            "language": "en",
        }
    ]
    status, body = _save("ManifestationShape", updated, original)
    assert status == 200
    assert body["success"] is True

    triples = _assert_entity_has_label_and_type(
        entity_id, LRMOO_F3_MANIFESTATION, "Test Manifestation Updated"
    )
    assert not any(
        t["predicate"] == RDFS_LABEL and t["object"] == "Test Manifestation" for t in triples
    )


def test_cards_are_edited_in_place_and_new_ones_named(api_client):
    """Changing a card's role keeps its IRI; a card added next to it gets a new one.

    The client lists only the label as changed: the payload's cards replace the
    stored ones regardless, so the edited card ends with one role, not two.
    """
    prefix = f"Cards {_make_suffix()}"
    person, role = _person_and_role(prefix)
    other_person, other_role = _person_and_role(f"{prefix} Other")
    work = _create("MusicalWorkShape", _work_data(f"{prefix} Work", _card(person, role)))
    (card,) = _objects(work, CORE_HAS_AGENT_ROLE)

    edited = {"@id": card, **_card(person, other_role)}
    data = {"@id": work, **_work_data(f"{prefix} Work renamed", edited, _card(other_person, role))}
    original = [
        {
            "predicate": RDFS_LABEL,
            "object": f"{prefix} Work",
            "objectType": "literal",
            "language": "en",
        }
    ]
    status, body = _save("MusicalWorkShape", data, original)
    assert status == 200, body
    assert body["success"] is True, body.get("validationReport")

    cards = _objects(work, CORE_HAS_AGENT_ROLE)
    assert card in cards and len(cards) == 2
    assert _objects(card, CORE_HAS_ROLE) == {other_role}
    (new,) = cards - {card}
    _assert_minted(new, "AgentRole")
    assert _objects(new, CORE_HAS_AGENT) == {other_person}


def test_another_records_card_is_refused(api_client):
    """A card belongs to one record: citing another Work's card is a 409."""
    prefix = f"Owned {_make_suffix()}"
    person, role = _person_and_role(prefix)
    first = _create("MusicalWorkShape", _work_data(f"{prefix} First", _card(person, role)))
    second = _create("MusicalWorkShape", _work_data(f"{prefix} Second"))
    (card,) = _objects(first, CORE_HAS_AGENT_ROLE)

    data = {"@id": second, **_work_data(f"{prefix} Second", {"@id": card, **_card(person, role)})}
    status, _ = _save("MusicalWorkShape", data, [])
    assert status == 409
    assert CORE_HAS_AGENT_ROLE not in {t["predicate"] for t in _triples(second)}


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------


def test_delete_is_refused_while_a_card_cites_the_record(api_client):
    """A Person in a Work's card cannot be deleted; the 409 names the Work."""
    prefix = f"Cited {_make_suffix()}"
    person, role = _person_and_role(prefix)
    work = _create("MusicalWorkShape", _work_data(f"{prefix} Work", _card(person, role)))

    status, body = _delete(person)
    assert status == 409, body
    assert work in body["detail"] and f"{prefix} Work" in body["detail"]
    _assert_entity_has_label_and_type(person, CORE_PERSON, f"{prefix} Person")


def test_delete_of_a_missing_record_is_a_404(api_client):
    """Deleting a record that was never created is reported as not found."""
    status, body = _delete(_data_iri(f"never_created_{_make_suffix()}"))
    assert status == 404, body


def test_delete_removes_the_record_and_its_cards(api_client):
    """An uncited Work goes, and its cards with it; the Person it cited stays."""
    prefix = f"Deleted {_make_suffix()}"
    person, role = _person_and_role(prefix)
    work = _create("MusicalWorkShape", _work_data(f"{prefix} Work", _card(person, role)))
    (card,) = _objects(work, CORE_HAS_AGENT_ROLE)

    status, body = _delete(work)
    assert status == 204, body
    _assert_not_stored(work)
    _assert_not_stored(card)
    _assert_entity_has_label_and_type(person, CORE_PERSON, f"{prefix} Person")


# ---------------------------------------------------------------------------
# Contributor polymorphic type tests
#
# ContributorShape targets foaf:Agent but constrains every focus node with
# `sh:or ( [sh:class foaf:Person] [sh:class foaf:Organization] )`. foaf:Agent
# alone never satisfies that constraint, so callers must assert one of the
# two concrete types alongside it -- see schema_extractor.typeOptions and
# jsonld.buildJsonLdEntity on the frontend, which drive this from a form
# selector instead of hardcoding it.
# ---------------------------------------------------------------------------


def _make_contributor_payload(label: str, concrete_type: str) -> dict:
    return {
        "@type": [FOAF_AGENT, concrete_type],
        RDFS_LABEL: _label(label),
        FOAF_NAME: _label(f"{label} Full Name"),
    }


def test_positive_contributor_person_insert_persists(api_client):
    """A Contributor typed [foaf:Agent, foaf:Person] satisfies sh:or and persists."""
    entity_id = _create("ContributorShape", _make_contributor_payload("Test Donor", FOAF_PERSON))
    assert _objects(entity_id, RDF_TYPE) == {FOAF_AGENT, FOAF_PERSON}


def test_positive_contributor_organization_insert_persists(api_client):
    """A Contributor typed [foaf:Agent, foaf:Organization] also satisfies sh:or."""
    entity_id = _create(
        "ContributorShape", _make_contributor_payload("Test Donor", FOAF_ORGANIZATION)
    )
    assert _objects(entity_id, RDF_TYPE) == {FOAF_AGENT, FOAF_ORGANIZATION}


def test_negative_contributor_missing_concrete_type_is_rejected(api_client):
    """A Contributor typed only foaf:Agent fails sh:or and is not persisted.

    This is the payload a client sends when it emits `@type = shape.targetClass`
    alone, which is what the typeOptions selector exists to prevent.
    """
    data = _make_contributor_payload("Test Donor", FOAF_PERSON)
    data["@type"] = FOAF_AGENT
    status, body = _save("ContributorShape", data)
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False
    _assert_not_stored(body["entityId"])


def test_contributor_appears_in_shape_scoped_list(api_client):
    """A persisted Contributor is found by its shape-scoped listing query.

    The list/count SPARQL for a shape does an exact `?id a <targetClassUri>`
    match with no subclass reasoning (see dataexplorer-backend/api/data.py), so the
    Contributor's @type array must keep foaf:Agent alongside the concrete
    type -- the concrete type alone satisfies sh:or but would silently drop
    the entity from every shape-scoped listing.
    """
    suffix = _make_suffix()
    entity_id = _create(
        "ContributorShape", _make_contributor_payload(f"Donor {suffix}", FOAF_PERSON)
    )
    query = urlencode({"shapeId": _shape_iri("ContributorShape"), "q": suffix})
    status, listing = _read_json(f"/api/v1/dataexplorer/entities?{query}")
    assert status == 200
    assert any(item["id"] == entity_id for item in listing["items"])


# ---------------------------------------------------------------------------
# Digital copies: upload on the writer, download from the reader
# ---------------------------------------------------------------------------

STAGE_FILE = "/api/v1/curator/files/staged"
META_FILES = "/api/v1/dataexplorer/meta/files"
P138I_HAS_REPRESENTATION = "http://www.cidoc-crm.org/cidoc-crm/P138i_has_representation"
XSD = "http://www.w3.org/2001/XMLSchema#"


def _make_pdf() -> bytes:
    """A one-page PDF, unique per call so its checksum identifies this run."""
    from pypdf import PdfWriter  # noqa: PLC0415 - only the upload test needs it

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": _make_suffix()})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _upload_pdf(name: str, data: bytes) -> tuple[int, dict]:
    """POST one file as multipart/form-data, the way the editor's file field does."""
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = UrlRequest(
        f"{API_BASE_URL}{STAGE_FILE}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get_bytes(url: str) -> tuple[int, bytes, Message]:
    """GET a URL and return status, body and headers, without decoding.

    The headers stay a ``Message``, whose lookups ignore case as HTTP does.
    """
    try:
        with urlopen(UrlRequest(url), timeout=20) as response:
            return response.status, response.read(), response.headers
    except HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def _digital_copy_node(staged: dict) -> dict:
    """The inline schema:DigitalDocument node the editor sends for a staged file."""
    return {
        "@id": staged["id"],
        "@type": SCHEMA_DIGITAL_DOCUMENT,
        SCHEMA_NAME: staged["name"],
        SCHEMA_ENCODING_FORMAT: "application/pdf",
        SCHEMA_CONTENT_URL: {"@value": staged["contentUrl"], "@type": f"{XSD}anyURI"},
        SCHEMA_CONTENT_SIZE: {
            "@value": str(staged["contentSize"]),
            "@type": f"{XSD}integer",
        },
        SCHEMA_SHA256: staged["sha256"],
    }


def test_uploaded_copy_is_served_by_the_reader_once_submitted(api_client):
    """A PDF staged on the writer is published by the reader only after its Source is saved.

    Covers what the unit tests fake: real multipart parsing, real S3 storage in
    Garage, the promotion from staged to registered on submit, and the reader
    finding the file through the store and serving the same bytes.
    """
    status, files = _read_json(META_FILES)
    assert status == 200
    if not files.get("configured"):
        pytest.skip("file storage not configured: run scripts/garage-init.sh")
    registered_before = files["registered"]["count"]

    pdf = _make_pdf()
    status, staged = _upload_pdf("live-suite.pdf", pdf)
    assert status == 200, staged
    assert staged["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert staged["contentSize"] == len(pdf)
    file_id = staged["fileId"]
    published_url = f"{READ_API_BASE_URL}/rdf/data/{file_id}/content"

    # Staged: the writer previews it, the reader does not publish it.
    status, preview, _ = _get_bytes(f"{API_BASE_URL}{staged['contentUrl']}")
    assert status == 200 and preview == pdf
    status, _, _ = _get_bytes(published_url)
    assert status == 404

    prefix = f"Upload {_make_suffix()}"
    chain = _work_chain(prefix)
    manifestation = _create(
        "ManifestationShape", _manifestation_data(f"{prefix} Manifestation", chain["expression"])
    )
    source = _source_data(prefix, manifestation)
    source[P138I_HAS_REPRESENTATION] = _digital_copy_node(staged)
    status, body = _save("SourceShape", source)
    assert status == 200, body
    assert body["success"] is True

    # Submitted: the reader serves the same bytes, the staged copy is gone.
    status, served, headers = _get_bytes(published_url)
    assert status == 200
    assert hashlib.sha256(served).hexdigest() == staged["sha256"]
    assert headers.get("X-RFDB-File-State") == "registered"
    status, _, _ = _get_bytes(f"{API_BASE_URL}{staged['contentUrl']}")
    assert status == 404

    status, files = _read_json(META_FILES)
    assert status == 200
    assert files["registered"]["count"] == registered_before + 1
