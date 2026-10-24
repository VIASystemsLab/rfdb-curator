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
(see ``api_client``). It writes into the stack's data graph and does not clean up:
run it against a dev stack whose store may be wiped afterwards.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import uuid
from collections.abc import Iterator
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import pytest

# Writes -> curator-backend; reads -> dataexplorer-backend. Override either to
# point the suite at a deployed stack.
API_BASE_URL = os.getenv("RFDB_API_BASE_URL", "http://localhost:8000")
READ_API_BASE_URL = os.getenv("RFDB_READ_API_BASE_URL", "http://localhost:8001")

# Route constants, so a path change is one edit and no line wraps.
WRITE_ENTITIES = "/api/v1/curator/entities"
READ_ENTITY = "/api/v1/dataexplorer/entities/get"
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
CONTRIBUTOR_SHAPE = "https://rosfeatr.eu/rdf/schema/ContributorShape"


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

    yield None


# ---------------------------------------------------------------------------
# Payload factories
#
# These build nested JSON-LD documents mirroring the LRMoo / Polifonia
# ontologies used by the SHACL shapes under test. Each "positive" factory
# produces a payload that should conform to its shape; each "invalid" factory
# starts from a valid payload and removes exactly the triple(s) needed to
# violate one specific constraint, so the resulting negative test can be
# traced back to a single expected cause.
# ---------------------------------------------------------------------------


def _make_agent_role(person_id: str, person_label: str, role_id: str, role_label: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{_make_suffix()}_agentrole",
        "@type": CORE_AGENT_ROLE,
        CORE_HAS_AGENT: {
            "@id": person_id,
            "@type": CORE_PERSON,
            RDFS_LABEL: {"@value": person_label, "@language": "en"},
        },
        CORE_HAS_ROLE: {
            "@id": role_id,
            "@type": CORE_ROLE,
            RDFS_LABEL: {"@value": role_label, "@language": "en"},
        },
    }


def _make_manifestation_payload(*, suffix: str, include_work_label: bool = True) -> dict:
    work = {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
        "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
        CORE_HAS_AGENT_ROLE: [
            _make_agent_role(
                person_id=f"https://rosfeatr.eu/rdf/data/{suffix}_person",
                person_label="Test Person",
                role_id=f"https://rosfeatr.eu/rdf/data/{suffix}_role",
                role_label="Test Role",
            )
        ],
    }
    if include_work_label:
        work[RDFS_LABEL] = {"@value": "Test Work", "@language": "en"}

    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation",
        "@type": LRMOO_F3_MANIFESTATION,
        RDFS_LABEL: {"@value": "Test Manifestation", "@language": "en"},
        LRMOO_R4_EMBODIES: {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
            "@type": LRMOO_F2_EXPRESSION,
            RDFS_LABEL: {"@value": "Test Expression", "@language": "en"},
            P148I_IS_COMPONENT_OF: work,
        },
    }


def _make_work_payload(suffix: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
        "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
        RDFS_LABEL: {"@value": "Chain Work", "@language": "en"},
        CORE_HAS_AGENT_ROLE: [
            _make_agent_role(
                person_id=f"https://rosfeatr.eu/rdf/data/{suffix}_person",
                person_label="Chain Person",
                role_id=f"https://rosfeatr.eu/rdf/data/{suffix}_role",
                role_label="Chain Role",
            )
        ],
    }


def _make_expression_payload(suffix: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
        "@type": LRMOO_F2_EXPRESSION,
        RDFS_LABEL: {"@value": "Chain Expression", "@language": "en"},
        P148I_IS_COMPONENT_OF: {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
            "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
            RDFS_LABEL: {"@value": "Chain Work", "@language": "en"},
            CORE_HAS_AGENT_ROLE: [
                _make_agent_role(
                    person_id=f"https://rosfeatr.eu/rdf/data/{suffix}_person",
                    person_label="Chain Person",
                    role_id=f"https://rosfeatr.eu/rdf/data/{suffix}_role",
                    role_label="Chain Role",
                )
            ],
        },
    }


def _make_manifestation_chain_payload(suffix: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation",
        "@type": LRMOO_F3_MANIFESTATION,
        RDFS_LABEL: {"@value": "Chain Manifestation", "@language": "en"},
        LRMOO_R4_EMBODIES: {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
            "@type": LRMOO_F2_EXPRESSION,
            RDFS_LABEL: {"@value": "Chain Expression", "@language": "en"},
            P148I_IS_COMPONENT_OF: {
                "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
                "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
                RDFS_LABEL: {"@value": "Chain Work", "@language": "en"},
                CORE_HAS_AGENT_ROLE: [
                    _make_agent_role(
                        person_id=f"https://rosfeatr.eu/rdf/data/{suffix}_person",
                        person_label="Chain Person",
                        role_id=f"https://rosfeatr.eu/rdf/data/{suffix}_role",
                        role_label="Chain Role",
                    )
                ],
            },
        },
    }


def _make_source_payload(suffix: str, manifestation_id: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_source",
        "@type": [
            "https://w3id.org/polifonia/ontology/source/Source",
            "http://iflastandards.info/ns/lrm/lrmoo/F5_Item",
        ],
        RDFS_LABEL: {"@value": "Chain Source", "@language": "en"},
        "http://www.w3.org/2000/01/rdf-schema#seeAlso": {
            "@id": "https://example.org/catalog/chain"
        },
        "http://prismstandard.org/namespaces/basic/2.0/publicationDate": {
            "@value": "1736",
            "@type": "http://www.w3.org/2001/XMLSchema#gYear",
        },
        "https://w3id.org/polifonia/ontology/core/hasType": {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_type",
            "@type": "https://w3id.org/polifonia/ontology/core/Type",
            RDFS_LABEL: {"@value": "Libretto a Stampa", "@language": "en"},
        },
        "http://purl.org/dc/terms/identifier": "Shelfmark-Chain",
        "http://www.cidoc-crm.org/cidoc-crm/P51_has_former_or_current_owner": {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_owner",
            "@type": "https://w3id.org/polifonia/ontology/core/Organization",
            RDFS_LABEL: {"@value": "Chain Library", "@language": "en"},
            "https://w3id.org/polifonia/ontology/core/hasPlace": {
                "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_place",
                "@type": "https://w3id.org/polifonia/ontology/core/Place",
                RDFS_LABEL: {"@value": "Chain City", "@language": "en"},
            },
        },
        "http://iflastandards.info/ns/lrm/lrmoo/R7_exemplifies": {
            "@id": manifestation_id,
            "@type": LRMOO_F3_MANIFESTATION,
            RDFS_LABEL: {"@value": "Chain Manifestation", "@language": "en"},
            LRMOO_R4_EMBODIES: {
                "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
                "@type": LRMOO_F2_EXPRESSION,
                RDFS_LABEL: {"@value": "Chain Expression", "@language": "en"},
                P148I_IS_COMPONENT_OF: {
                    "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
                    "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
                    RDFS_LABEL: {"@value": "Chain Work", "@language": "en"},
                },
            },
        },
    }


def _make_manifestation_reference_only_payload(suffix: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation_ref_only",
        "@type": LRMOO_F3_MANIFESTATION,
        RDFS_LABEL: {"@value": "Chain Manifestation Ref Only", "@language": "en"},
        LRMOO_R4_EMBODIES: {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
        },
    }


def _make_invalid_expression_payload(suffix: str) -> dict:
    """Valid manifestation payload with the nested work's rdfs:label stripped.

    Targets the ExpressionShape/WorkShape min-count-on-label constraint via
    a cascade insert rooted at ManifestationShape.
    """
    payload = _make_manifestation_payload(suffix=suffix)
    work = payload[LRMOO_R4_EMBODIES][P148I_IS_COMPONENT_OF]
    work.pop(RDFS_LABEL, None)
    return payload


def _make_invalid_agent_role_payload(suffix: str) -> dict:
    """Valid manifestation payload with both hasAgent and hasRole stripped
    from the nested agent-role node.

    Note: this removes two properties at once, so a failure here confirms
    *some* AgentRoleShape constraint fired, not specifically which one. If
    you need to verify hasAgent and hasRole are independently enforced,
    split this into two payload factories that each remove only one.
    """
    payload = _make_manifestation_payload(suffix=suffix)
    agent_role = payload[LRMOO_R4_EMBODIES][P148I_IS_COMPONENT_OF][CORE_HAS_AGENT_ROLE][0]
    agent_role.pop(CORE_HAS_AGENT, None)
    agent_role.pop(CORE_HAS_ROLE, None)
    return payload


def _assert_entity_has_label_and_type(entity_id: str, expected_type: str, expected_label: str):
    status, body = _read_json(_entity_url(entity_id))
    assert status == 200
    triples = body["triples"]
    assert any(t["predicate"] == RDFS_LABEL and t["object"] == expected_label for t in triples)
    assert any(t["predicate"] == RDF_TYPE and t["object"] == expected_type for t in triples)
    return triples


def _collect_all_ids(node) -> set[str]:
    """Recursively collect every @id in a nested JSON-LD structure.

    Used to verify full-cascade rollback on a rejected insert: rather than
    hardcoding the suffix -> IRI naming scheme used by each payload factory
    (which would silently go stale if a factory changed), this walks the
    actual payload sent to the API and returns every IRI it touches. That
    set is exactly "everything the backend would have had to persist for
    this insert to fully succeed" -- and therefore exactly what must NOT
    exist in the store after the insert is rejected.
    """
    ids: set[str] = set()
    if isinstance(node, dict):
        node_id = node.get("@id")
        if isinstance(node_id, str) and node_id.startswith("http"):
            ids.add(node_id)
        for value in node.values():
            ids |= _collect_all_ids(value)
    elif isinstance(node, list):
        for item in node:
            ids |= _collect_all_ids(item)
    return ids


# ---------------------------------------------------------------------------
# Positive insert tests
# ---------------------------------------------------------------------------


def test_positive_manifestation_insert_persists(api_client):
    """POST /api/v1/curator/entities persists a valid manifestation with required links."""
    suffix = _make_suffix()
    payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": _make_manifestation_payload(suffix=suffix),
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200

    assert body["success"] is True
    assert body["entityId"] == f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation"
    assert body["validationReport"]["conforms"] is True

    triples = _assert_entity_has_label_and_type(
        body["entityId"], LRMOO_F3_MANIFESTATION, "Test Manifestation"
    )
    assert any(t["predicate"] == LRMOO_R4_EMBODIES for t in triples)


def test_positive_work_insert_persists(api_client):
    """POST /api/v1/curator/entities persists a standalone work with an agent-role relation."""
    suffix = _make_suffix()
    payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/MusicalWorkShape",
        "data": {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
            "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
            RDFS_LABEL: {"@value": "Standalone Work", "@language": "en"},
            CORE_HAS_AGENT_ROLE: [
                _make_agent_role(
                    person_id=f"https://rosfeatr.eu/rdf/data/{suffix}_person",
                    person_label="Composer Person",
                    role_id=f"https://rosfeatr.eu/rdf/data/{suffix}_role",
                    role_label="Composer Role",
                )
            ],
        },
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is True

    triples = _assert_entity_has_label_and_type(body["entityId"], LRMOO_F1_WORK, "Standalone Work")
    assert any(t["predicate"] == CORE_HAS_AGENT_ROLE for t in triples)


def test_positive_source_insert_persists(api_client):
    """POST /api/v1/curator/entities persists a valid source including publication metadata."""
    suffix = _make_suffix()
    payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/SourceShape",
        "data": {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_source",
            "@type": [
                "https://w3id.org/polifonia/ontology/source/Source",
                "http://iflastandards.info/ns/lrm/lrmoo/F5_Item",
            ],
            RDFS_LABEL: {"@value": "Source Example", "@language": "en"},
            "http://www.w3.org/2000/01/rdf-schema#seeAlso": {
                "@id": "https://example.org/catalog/123"
            },
            "http://prismstandard.org/namespaces/basic/2.0/publicationDate": {
                "@value": "1736",
                "@type": "http://www.w3.org/2001/XMLSchema#gYear",
            },
            "https://w3id.org/polifonia/ontology/core/hasType": {
                "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_type",
                "@type": "https://w3id.org/polifonia/ontology/core/Type",
                RDFS_LABEL: {"@value": "Libretto a Stampa", "@language": "en"},
            },
            "http://purl.org/dc/terms/identifier": "Shelfmark-123",
            "http://www.cidoc-crm.org/cidoc-crm/P51_has_former_or_current_owner": {
                "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_owner",
                "@type": "https://w3id.org/polifonia/ontology/core/Organization",
                RDFS_LABEL: {"@value": "Test Library", "@language": "en"},
                "https://w3id.org/polifonia/ontology/core/hasPlace": {
                    "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_place",
                    "@type": "https://w3id.org/polifonia/ontology/core/Place",
                    RDFS_LABEL: {"@value": "Test City", "@language": "en"},
                },
            },
            "http://iflastandards.info/ns/lrm/lrmoo/R7_exemplifies": {
                "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation",
                "@type": LRMOO_F3_MANIFESTATION,
                RDFS_LABEL: {"@value": "Source Manifestation", "@language": "en"},
                LRMOO_R4_EMBODIES: {
                    "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
                    "@type": LRMOO_F2_EXPRESSION,
                    RDFS_LABEL: {"@value": "Source Expression", "@language": "en"},
                    P148I_IS_COMPONENT_OF: {
                        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
                        "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
                        RDFS_LABEL: {"@value": "Source Work", "@language": "en"},
                    },
                },
            },
        },
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is True

    triples = _assert_entity_has_label_and_type(
        body["entityId"],
        "https://w3id.org/polifonia/ontology/source/Source",
        "Source Example",
    )
    assert any(
        t["predicate"] == "http://prismstandard.org/namespaces/basic/2.0/publicationDate"
        for t in triples
    )


def test_full_stack_valid_insert_sequence(api_client):
    """Sequential Work->Expression->Manifestation->Source inserts stay valid end-to-end."""
    suffix = _make_suffix()

    work_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/MusicalWorkShape",
        "data": _make_work_payload(suffix),
    }
    work_status, work_body = _request_json("POST", WRITE_ENTITIES, work_payload)
    assert work_status == 200
    assert work_body["success"] is True
    _assert_entity_has_label_and_type(work_body["entityId"], LRMOO_F1_WORK, "Chain Work")

    expression_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ExpressionShape",
        "data": _make_expression_payload(suffix),
    }
    expression_status, expression_body = _request_json("POST", WRITE_ENTITIES, expression_payload)
    assert expression_status == 200
    assert expression_body["success"] is True
    _assert_entity_has_label_and_type(
        expression_body["entityId"], LRMOO_F2_EXPRESSION, "Chain Expression"
    )

    manifestation_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": _make_manifestation_chain_payload(suffix),
    }
    manifestation_status, manifestation_body = _request_json(
        "POST", WRITE_ENTITIES, manifestation_payload
    )
    assert manifestation_status == 200
    assert manifestation_body["success"] is True
    _assert_entity_has_label_and_type(
        manifestation_body["entityId"], LRMOO_F3_MANIFESTATION, "Chain Manifestation"
    )

    source_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/SourceShape",
        "data": _make_source_payload(suffix, manifestation_body["entityId"]),
    }
    source_status, source_body = _request_json("POST", WRITE_ENTITIES, source_payload)
    assert source_status == 200
    assert source_body["success"] is True

    triples = _assert_entity_has_label_and_type(
        source_body["entityId"],
        "https://w3id.org/polifonia/ontology/source/Source",
        "Chain Source",
    )
    assert any(
        t["predicate"] == "http://iflastandards.info/ns/lrm/lrmoo/R7_exemplifies" for t in triples
    )


def test_manifestation_reference_only_insert_uses_merged_context(api_client):
    """Reference-only manifestation insert succeeds via backend merge context fetch.

    The manifestation payload here supplies only an @id for the embodied
    expression (no inline properties). For this to validate against
    ExpressionShape, the backend must fetch the expression's already-stored
    triples (inserted by the two calls above) and merge them with the
    incoming payload before running SHACL -- a stub reference can't satisfy
    a shape's constraints on its own.
    """
    suffix = _make_suffix()

    work_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/MusicalWorkShape",
        "data": _make_work_payload(suffix),
    }
    work_status, work_body = _request_json("POST", WRITE_ENTITIES, work_payload)
    assert work_status == 200
    assert work_body["success"] is True

    expression_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ExpressionShape",
        "data": _make_expression_payload(suffix),
    }
    expression_status, expression_body = _request_json("POST", WRITE_ENTITIES, expression_payload)
    assert expression_status == 200
    assert expression_body["success"] is True

    manifestation_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": _make_manifestation_reference_only_payload(suffix),
    }
    manifestation_status, manifestation_body = _request_json(
        "POST", WRITE_ENTITIES, manifestation_payload
    )
    assert manifestation_status == 200
    assert manifestation_body["success"] is True

    triples = _assert_entity_has_label_and_type(
        manifestation_body["entityId"],
        LRMOO_F3_MANIFESTATION,
        "Chain Manifestation Ref Only",
    )
    assert any(t["predicate"] == LRMOO_R4_EMBODIES for t in triples)


# ---------------------------------------------------------------------------
# Negative insert tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload_factory",
    [
        _make_invalid_expression_payload,
        _make_invalid_agent_role_payload,
    ],
)
def test_negative_manifestation_insert_is_rejected(api_client, payload_factory):
    """Invalid manifestation payloads are rejected and not persisted in store.

    Note: this only checks the top-level manifestation IRI. It does not
    check whether the nested work/expression/person/role IRIs the same
    cascade would have touched were left un-persisted -- see
    test_negative_cascade_insert_rolls_back_every_nested_entity below for
    that stronger, full-transaction check.
    """
    suffix = _make_suffix()
    payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": payload_factory(suffix),
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False
    assert body["validationReport"]["violations"]

    stored_status, _ = _read_text(_entity_url(payload["data"]["@id"]))
    assert stored_status == 404


def test_negative_cascade_insert_rolls_back_every_nested_entity(api_client):
    """A validation failure anywhere in a cascade must leave no trace anywhere
    in the cascade -- not just at the top-level focus node.

    test_negative_manifestation_insert_is_rejected only checks that the
    manifestation IRI is absent after rejection. It asserts nothing about
    the work, expression, person, role, or agent-role nodes the same POST
    would have created had validation passed. If the backend writes children
    before validating the full closure, or validates node-by-node instead of
    as a single transaction, those nodes can survive a "rejected" insert as
    orphaned triples with no entity properly referencing them -- a
    transactional-integrity bug this suite would otherwise never catch.

    This also strengthens the violation check itself: instead of asserting
    `violations` is merely non-empty, it confirms the report actually
    implicates the node/property that was removed, so a spurious violation
    caused by an unrelated bug can't masquerade as the expected rejection.
    """
    suffix = _make_suffix()
    payload_data = _make_invalid_expression_payload(suffix)  # nested work is missing rdfs:label
    all_ids = _collect_all_ids(payload_data)
    # Sanity check on the test fixture itself: if this cascade doesn't touch
    # at least manifestation + expression + work + person + role, the payload
    # factory has changed shape and this test needs to be revisited.
    assert len(all_ids) >= 5

    status, body = _request_json(
        "POST",
        WRITE_ENTITIES,
        {"shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape", "data": payload_data},
    )
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False
    assert body["validationReport"]["violations"]

    # The report must implicate the actual failing node/property, not just
    # contain *some* violation. The backend exposes violations with explicit
    # `focusNode` and `path` keys.
    #
    # FUTURE / consider improving: the missing work label can be reported more
    # than once. ExpressionShape declares cidoc:P148i_is_component_of with
    # sh:node MusicalWorkShape, so the same work-label violation surfaces both via
    # MusicalWorkShape's targetClass and via the Expression's sh:node traversal.
    # `any(...)` tolerates duplicates, so this test is unaffected, but do NOT
    # tighten it to assert an exact violation *count* without first
    # de-duplicating by (focusNode, path).
    violations = body["validationReport"]["violations"]
    work_id = f"https://rosfeatr.eu/rdf/data/{suffix}_work"
    assert any(v.get("focusNode") == work_id and v.get("path") == RDFS_LABEL for v in violations), (
        "validation report doesn't reference the expected failing work node and rdfs:label path"
    )

    # Full-graph rollback: nothing in the cascade should have been
    # persisted, not just the manifestation.
    for entity_id in all_ids:
        stored_status, _ = _read_text(_entity_url(entity_id))
        assert stored_status == 404, (
            f"orphaned triples found for {entity_id} after a rejected "
            f"cascade insert -- transaction is not atomic"
        )


def test_negative_work_insert_without_label_is_rejected(api_client):
    """Work missing required label fails SHACL validation and is not stored."""
    suffix = _make_suffix()
    payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/MusicalWorkShape",
        "data": {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_work",
            "@type": [MM_MUSIC_ENTITY, LRMOO_F1_WORK],
            CORE_HAS_AGENT_ROLE: [
                _make_agent_role(
                    person_id=f"https://rosfeatr.eu/rdf/data/{suffix}_person",
                    person_label="Composer Person",
                    role_id=f"https://rosfeatr.eu/rdf/data/{suffix}_role",
                    role_label="Composer Role",
                )
            ],
        },
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False

    stored_status, _ = _read_text(_entity_url(payload["data"]["@id"]))
    assert stored_status == 404


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------


def test_failed_update_keeps_existing_entity(api_client):
    """Failed update leaves previously persisted entity triples unchanged."""
    suffix = _make_suffix()
    entity_id = f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation"
    valid_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": _make_manifestation_payload(suffix=suffix),
    }

    create_status, create_body = _request_json("POST", WRITE_ENTITIES, valid_payload)
    assert create_status == 200
    assert create_body["success"] is True

    invalid_update = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": _make_manifestation_payload(suffix=suffix, include_work_label=False),
        "originalTriples": [
            {
                "predicate": LRMOO_R4_EMBODIES,
                "object": f"https://rosfeatr.eu/rdf/data/{suffix}_expression",
                "objectType": "iri",
            }
        ],
    }
    invalid_update["data"].pop(RDFS_LABEL, None)

    update_status, body = _request_json("POST", WRITE_ENTITIES, invalid_update)
    assert update_status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False

    stored_status, stored_body = _read_json(_entity_url(entity_id))
    assert stored_status == 200
    triples = stored_body["triples"]
    assert any(
        t["predicate"] == RDFS_LABEL and t["object"] == "Test Manifestation" for t in triples
    )
    assert any(t["predicate"] == LRMOO_R4_EMBODIES for t in triples)


def test_valid_update_replaces_label_without_dropping_type(api_client):
    """Valid update replaces label while preserving rdf:type and other structure."""
    suffix = _make_suffix()
    entity_id = f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation"
    create_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": _make_manifestation_payload(suffix=suffix),
    }

    create_status, create_body = _request_json("POST", WRITE_ENTITIES, create_payload)
    assert create_status == 200
    assert create_body["success"] is True

    updated_data = json.loads(json.dumps(create_payload["data"]))
    updated_data[RDFS_LABEL] = {
        "@value": "Test Manifestation Updated",
        "@language": "en",
    }
    update_payload = {
        "shapeId": "https://rosfeatr.eu/rdf/schema/ManifestationShape",
        "data": updated_data,
        "originalTriples": [
            {
                "predicate": RDFS_LABEL,
                "object": "Test Manifestation",
                "objectType": "literal",
                "language": "en",
            }
        ],
    }

    update_status, update_body = _request_json("POST", WRITE_ENTITIES, update_payload)
    assert update_status == 200
    assert update_body["success"] is True

    triples = _assert_entity_has_label_and_type(
        entity_id, LRMOO_F3_MANIFESTATION, "Test Manifestation Updated"
    )
    assert not any(
        t["predicate"] == RDFS_LABEL and t["object"] == "Test Manifestation" for t in triples
    )


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


def _make_contributor_payload(suffix: str, concrete_type: str) -> dict:
    return {
        "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_contributor",
        "@type": [FOAF_AGENT, concrete_type],
        RDFS_LABEL: {"@value": "Test Donor", "@language": "en"},
        FOAF_NAME: {"@value": "Test Donor Full Name", "@language": "en"},
    }


def test_positive_contributor_person_insert_persists(api_client):
    """A Contributor typed [foaf:Agent, foaf:Person] satisfies sh:or and persists."""
    suffix = _make_suffix()
    payload = {
        "shapeId": CONTRIBUTOR_SHAPE,
        "data": _make_contributor_payload(suffix, FOAF_PERSON),
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is True, body.get("validationReport")

    entity_id = payload["data"]["@id"]
    stored_status, stored_body = _read_json(_entity_url(entity_id))
    assert stored_status == 200
    types = {t["object"] for t in stored_body["triples"] if t["predicate"] == RDF_TYPE}
    assert types == {FOAF_AGENT, FOAF_PERSON}


def test_positive_contributor_organization_insert_persists(api_client):
    """A Contributor typed [foaf:Agent, foaf:Organization] also satisfies sh:or."""
    suffix = _make_suffix()
    payload = {
        "shapeId": CONTRIBUTOR_SHAPE,
        "data": _make_contributor_payload(suffix, FOAF_ORGANIZATION),
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is True, body.get("validationReport")

    entity_id = payload["data"]["@id"]
    stored_status, stored_body = _read_json(_entity_url(entity_id))
    assert stored_status == 200
    types = {t["object"] for t in stored_body["triples"] if t["predicate"] == RDF_TYPE}
    assert types == {FOAF_AGENT, FOAF_ORGANIZATION}


def test_negative_contributor_missing_concrete_type_is_rejected(api_client):
    """A Contributor typed only foaf:Agent fails sh:or and is not persisted.

    This is the payload a client sends when it emits `@type = shape.targetClass`
    alone, which is what the typeOptions selector exists to prevent.
    """
    suffix = _make_suffix()
    payload = {
        "shapeId": CONTRIBUTOR_SHAPE,
        "data": {
            "@id": f"https://rosfeatr.eu/rdf/data/{suffix}_contributor",
            "@type": FOAF_AGENT,
            RDFS_LABEL: {"@value": "Test Donor", "@language": "en"},
            FOAF_NAME: {"@value": "Test Donor Full Name", "@language": "en"},
        },
    }

    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is False
    assert body["validationReport"]["conforms"] is False

    stored_status, _ = _read_text(_entity_url(payload["data"]["@id"]))
    assert stored_status == 404


def test_contributor_appears_in_shape_scoped_list(api_client):
    """A persisted Contributor is found by its shape-scoped listing query.

    The list/count SPARQL for a shape does an exact `?id a <targetClassUri>`
    match with no subclass reasoning (see dataexplorer-backend/api/data.py), so the
    Contributor's @type array must keep foaf:Agent alongside the concrete
    type -- the concrete type alone satisfies sh:or but would silently drop
    the entity from every shape-scoped listing.
    """
    suffix = _make_suffix()
    payload = {
        "shapeId": CONTRIBUTOR_SHAPE,
        "data": _make_contributor_payload(suffix, FOAF_PERSON),
    }
    status, body = _request_json("POST", WRITE_ENTITIES, payload)
    assert status == 200
    assert body["success"] is True

    entity_id = payload["data"]["@id"]
    query = urlencode({"shapeId": CONTRIBUTOR_SHAPE, "q": suffix})
    status, listing = _read_json(f"/api/v1/dataexplorer/entities?{query}")
    assert status == 200
    assert any(item["id"] == entity_id for item in listing["items"])


# ---------------------------------------------------------------------------
# Digital copies: upload on the writer, download from the reader
# ---------------------------------------------------------------------------

STAGE_FILE = "/api/v1/curator/files/staged"
META_FILES = "/api/v1/dataexplorer/meta/files"
SOURCE_SHAPE = "https://rosfeatr.eu/rdf/schema/SourceShape"
P138I_HAS_REPRESENTATION = "http://www.cidoc-crm.org/cidoc-crm/P138i_has_representation"
SCHEMA_ORG = "http://schema.org/"
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
        "@type": f"{SCHEMA_ORG}DigitalDocument",
        f"{SCHEMA_ORG}name": staged["name"],
        f"{SCHEMA_ORG}encodingFormat": "application/pdf",
        f"{SCHEMA_ORG}contentUrl": {"@value": staged["contentUrl"], "@type": f"{XSD}anyURI"},
        f"{SCHEMA_ORG}contentSize": {
            "@value": str(staged["contentSize"]),
            "@type": f"{XSD}integer",
        },
        f"{SCHEMA_ORG}sha256": staged["sha256"],
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

    suffix = _make_suffix()
    source = _make_source_payload(suffix, f"https://rosfeatr.eu/rdf/data/{suffix}_manifestation")
    source[P138I_HAS_REPRESENTATION] = _digital_copy_node(staged)
    status, body = _request_json("POST", WRITE_ENTITIES, {"shapeId": SOURCE_SHAPE, "data": source})
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
