"""Nested AgentRole editing: the bridge node is validated and written intact.

AgentRole is a helper/bridge shape (no rdfs:label). It is created inline as part
of its parent (Work/Expression) and must carry an explicit ``core:AgentRole``
@type to be validated (class-targeting) and to reference exactly one Person and
one Role. These tests create a Work with a nested AgentRole and confirm the
bridge node keeps its stable IRI in the written Turtle — the property that lets
an update re-reference it instead of regenerating it — and that dropping the
explicit @type is rejected.

Removal is covered too: unlinking a bridge node must delete the node itself, not
just the parent's link, or the editor keeps showing a connection the curator
removed and an orphan stays in the store.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from api.data import create_or_update_entity
from core.shacl_validator import ShaclValidator
from core.validation_merge import _build_shape_dep_graph
from models.data import EntityData, TripleObject
from rfdb_core.schema_extractor import SchemaExtractor

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rfdb_schema.ttl"


CORE = "https://w3id.org/polifonia/ontology/core/"
DATA = "https://rosfeatr.eu/rdf/data/"
WORK_SHAPE = "https://rosfeatr.eu/rdf/schema/MusicalWorkShape"

HAS_AGENT_ROLE = URIRef(CORE + "hasAgentRole")
HAS_AGENT = URIRef(CORE + "hasAgent")
HAS_ROLE = URIRef(CORE + "hasRole")
AGENT_ROLE = URIRef(CORE + "AgentRole")


class _CapturingOxigraph:
    """Oxigraph double: validates via the real SHACL validator and captures the
    Turtle that would be written."""

    def __init__(self) -> None:
        self.loaded_turtle = ""

    def from_clause(self) -> str:
        """Empty SPARQL FROM clause for tests."""
        return ""

    def construct(self, _sparql: str) -> Graph:
        """No pre-existing triples for the payload under test."""
        return Graph()

    def load_turtle(self, turtle: str) -> None:
        """Capture the Turtle that would be loaded into Oxigraph."""
        self.loaded_turtle = turtle


def _request(oxigraph: _CapturingOxigraph) -> SimpleNamespace:
    """Minimal request object expected by create_or_update_entity."""
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=oxigraph,
                shape_dep_graph={},
                shacl_validator=ShaclValidator(str(SCHEMA_PATH)),
            )
        )
    )


def _work_with_agent_role() -> EntityData:
    """A Work with one inline AgentRole linking a Person to a Role, all named."""
    return EntityData(
        shapeId=WORK_SHAPE,
        data={
            "@context": {
                "mm": "https://w3id.org/polifonia/ontology/music-meta/",
                "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "core": CORE,
            },
            "@id": DATA + "nested_work",
            "@type": ["mm:MusicEntity", "lrmoo:F1_Work"],
            "rdfs:label": {"@value": "Nested Work", "@language": "en"},
            "core:hasAgentRole": {
                "@id": DATA + "nested_work_ar_0",
                "@type": "core:AgentRole",
                "core:hasAgent": {
                    "@id": DATA + "person_x",
                    "@type": "core:Person",
                    "rdfs:label": {"@value": "Composer X", "@language": "en"},
                },
                "core:hasRole": {
                    "@id": DATA + "role_composer",
                    "@type": "core:Role",
                    "rdfs:label": {"@value": "Composer", "@language": "en"},
                },
            },
        },
        originalTriples=None,
    )


def test_nested_agent_role_validates_and_is_written_intact() -> None:
    """A Work with an inline AgentRole conforms and the bridge triples persist."""
    oxigraph = _CapturingOxigraph()
    response = create_or_update_entity(_work_with_agent_role(), _request(oxigraph))
    assert response.success is True
    assert response.validationReport.conforms is True

    written = Graph().parse(data=oxigraph.loaded_turtle, format="turtle")
    work = URIRef(DATA + "nested_work")
    agent_role = URIRef(DATA + "nested_work_ar_0")

    # The bridge node keeps its stable IRI (a named node, not a blank/regenerated one).
    assert (work, HAS_AGENT_ROLE, agent_role) in written
    assert (agent_role, RDF.type, AGENT_ROLE) in written
    assert (agent_role, HAS_AGENT, URIRef(DATA + "person_x")) in written
    assert (agent_role, HAS_ROLE, URIRef(DATA + "role_composer")) in written


def test_agent_role_without_type_is_rejected() -> None:
    """Dropping the explicit core:AgentRole @type makes the write fail: the parent
    link's sh:class/sh:node require a conforming AgentRole, which a node with no
    type cannot be."""
    payload = _work_with_agent_role()
    del payload.data["core:hasAgentRole"]["@type"]
    response = create_or_update_entity(payload, _request(_CapturingOxigraph()))
    assert response.success is False


class _GraphOxigraph:
    """Oxigraph double backed by a real rdflib graph, so updates actually run.

    ``_CapturingOxigraph`` above is enough for create flows; removal is an update
    and needs the store to hold pre-existing triples that the DELETE has to hit.
    """

    def __init__(self, turtle: str = "") -> None:
        self.g = Graph()
        if turtle:
            self.g.parse(data=turtle, format="turtle")

    def from_clause(self) -> str:
        """Empty SPARQL FROM clause (the double has no named graph)."""
        return ""

    def with_clause(self) -> str:
        """Empty SPARQL WITH clause (the double has no named graph)."""
        return ""

    def construct(self, sparql: str) -> Graph:
        """Run the CONSTRUCT against the in-memory graph."""
        return self.g.query(sparql).graph or Graph()

    def load_turtle(self, turtle: str) -> None:
        """Merge the written Turtle into the in-memory graph."""
        self.g.parse(data=turtle, format="turtle")

    def update(self, sparql: str) -> None:
        """Run a SPARQL update against the in-memory graph."""
        self.g.update(sparql)


_STORED_WORK = f"""
@prefix core: <{CORE}> .
@prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix mm: <https://w3id.org/polifonia/ontology/music-meta/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rfdb: <{DATA}> .

rfdb:stored_work a mm:MusicEntity, lrmoo:F1_Work ;
  rdfs:label "Stored Work"@en ;
  core:hasAgentRole rfdb:stored_work_ar_0 .

rfdb:stored_work_ar_0 a core:AgentRole ;
  core:hasAgent rfdb:person_y ;
  core:hasRole rfdb:role_librettist .

rfdb:person_y a core:Person ;
  rdfs:label "Librettist Y"@en .

rfdb:role_librettist a core:Role ;
  rdfs:label "Librettist"@en .
"""


def _work_without_agent_role() -> EntityData:
    """The stored work re-saved with its only connection removed in the form."""
    return EntityData(
        shapeId=WORK_SHAPE,
        data={
            "@context": {
                "mm": "https://w3id.org/polifonia/ontology/music-meta/",
                "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "core": CORE,
            },
            "@id": DATA + "stored_work",
            "@type": ["mm:MusicEntity", "lrmoo:F1_Work"],
            "rdfs:label": {"@value": "Stored Work", "@language": "en"},
        },
        originalTriples=[
            TripleObject(
                predicate=str(HAS_AGENT_ROLE),
                object=DATA + "stored_work_ar_0",
                objectType="iri",
            )
        ],
    )


def _dep_request(oxigraph: _GraphOxigraph) -> SimpleNamespace:
    """Request object carrying the real shape dependency graph."""
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=oxigraph,
                shape_dep_graph=_build_shape_dep_graph(extractor),
                shacl_validator=ShaclValidator(str(SCHEMA_PATH)),
            )
        )
    )


def test_removing_agent_role_drops_link_and_orphan_bridge_node() -> None:
    """Removing a connection in the editor deletes the link *and* the bridge node.

    The bridge node exists only to connect the work to a person/role, so leaving
    its triples behind would keep an orphan in the store that later validation
    merges can pull back in.  The person and role it referenced are standalone
    entities and must survive.
    """
    store = _GraphOxigraph(_STORED_WORK)
    response = create_or_update_entity(_work_without_agent_role(), _dep_request(store))
    assert response.success is True

    work = URIRef(DATA + "stored_work")
    agent_role = URIRef(DATA + "stored_work_ar_0")

    assert (work, HAS_AGENT_ROLE, agent_role) not in store.g
    assert list(store.g.triples((agent_role, None, None))) == []
    # Standalone entities are untouched.
    assert (URIRef(DATA + "person_y"), RDF.type, URIRef(CORE + "Person")) in store.g
    assert (URIRef(DATA + "role_librettist"), RDF.type, URIRef(CORE + "Role")) in store.g


def test_shared_bridge_node_survives_removal_from_one_parent() -> None:
    """A bridge node still referenced by another entity keeps its own triples."""
    store = _GraphOxigraph(
        _STORED_WORK
        + """
rfdb:other_work a mm:MusicEntity, lrmoo:F1_Work ;
  rdfs:label "Other Work"@en ;
  core:hasAgentRole rfdb:stored_work_ar_0 .
"""
    )
    response = create_or_update_entity(_work_without_agent_role(), _dep_request(store))
    assert response.success is True

    agent_role = URIRef(DATA + "stored_work_ar_0")
    assert (URIRef(DATA + "stored_work"), HAS_AGENT_ROLE, agent_role) not in store.g
    assert (URIRef(DATA + "other_work"), HAS_AGENT_ROLE, agent_role) in store.g
    assert (agent_role, HAS_AGENT, URIRef(DATA + "person_y")) in store.g


def test_editing_the_person_on_a_connection_replaces_the_old_one() -> None:
    """Re-pointing an existing connection at another person replaces the value.

    The bridge node keeps its IRI, so the store already holds ``hasAgent
    person_y`` while the payload asserts ``hasAgent person_z``. Both the
    validation merge and the write must treat the payload as authoritative for a
    bridge node it describes, or the save is rejected for exceeding
    ``sh:maxCount 1`` on a node the curator only edited.
    """
    store = _GraphOxigraph(
        _STORED_WORK
        + """
rfdb:person_z a core:Person ;
  rdfs:label "Librettist Z"@en .
"""
    )
    payload = _work_without_agent_role()
    payload.data["core:hasAgentRole"] = {
        "@id": DATA + "stored_work_ar_0",
        "@type": "core:AgentRole",
        "core:hasAgent": {"@id": DATA + "person_z"},
        "core:hasRole": {"@id": DATA + "role_librettist"},
    }

    response = create_or_update_entity(payload, _dep_request(store))
    assert response.validationReport.conforms is True
    assert response.success is True

    agent_role = URIRef(DATA + "stored_work_ar_0")
    assert (agent_role, HAS_AGENT, URIRef(DATA + "person_z")) in store.g
    assert (agent_role, HAS_AGENT, URIRef(DATA + "person_y")) not in store.g
    assert (agent_role, HAS_ROLE, URIRef(DATA + "role_librettist")) in store.g


class _FailingWriteOxigraph(_GraphOxigraph):
    """Store whose first bulk load fails, so the write path has to roll back."""

    def __init__(self, turtle: str = "") -> None:
        super().__init__(turtle)
        self.loads = 0

    def load_turtle(self, turtle: str) -> None:
        """Fail the write, then accept the rollback load."""
        self.loads += 1
        if self.loads == 1:
            raise OSError("simulated store write failure")
        super().load_turtle(turtle)


def test_failed_write_restores_the_removed_bridge_node() -> None:
    """A failed load after a bridge removal restores the node, not only the link.

    The delete runs before the load, so if the load fails the rollback is all
    that stands between the curator and a connection whose node lost its person
    and role.
    """
    store = _FailingWriteOxigraph(_STORED_WORK)
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(_work_without_agent_role(), _dep_request(store))
    assert exc_info.value.status_code == 503

    work = URIRef(DATA + "stored_work")
    agent_role = URIRef(DATA + "stored_work_ar_0")
    assert (work, HAS_AGENT_ROLE, agent_role) in store.g
    assert (agent_role, RDF.type, AGENT_ROLE) in store.g
    assert (agent_role, HAS_AGENT, URIRef(DATA + "person_y")) in store.g
    assert (agent_role, HAS_ROLE, URIRef(DATA + "role_librettist")) in store.g
