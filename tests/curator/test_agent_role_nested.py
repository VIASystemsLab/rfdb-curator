"""Writing records and their bridge nodes: what a save and a delete may touch.

AgentRole is a helper/bridge shape (no rdfs:label). It is created inline as part
of its parent (Work/Expression/Performance), belongs to that one record, and must
carry an explicit ``core:AgentRole`` @type to be validated (class-targeting) and
to reference exactly one Person and one Role.

The backend names every new node: a record sent without ``@id`` and a card sent
as a blank node both get ``<DATA_BASE_URI><Shape>_<12hex>``. A save describes its
record and the record's cards only; a card with an IRI must already be this
record's. Removing a card deletes the node, not just the link; deleting a record
deletes its cards, and is refused while other records point to it.

The store is an in-memory rdflib graph, validation is the real SHACL schema.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from api.data import create_or_update_entity, delete_entity
from core.shacl_validator import ShaclValidator
from core.validation_merge import build_shape_dep_graph
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
P148I = "http://www.cidoc-crm.org/cidoc-crm/P148i_is_component_of"


_CONTEXT = {
    "mm": "https://w3id.org/polifonia/ontology/music-meta/",
    "lrmoo": "http://iflastandards.info/ns/lrm/lrmoo/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "core": CORE,
}


def _card(person: str, role: str, card_id: str | None = None) -> dict:
    """An AgentRole card as the editor sends it; new cards have no ``@id``."""
    card = {
        "@type": "core:AgentRole",
        "core:hasAgent": {"@id": DATA + person},
        "core:hasRole": {"@id": DATA + role},
    }
    if card_id:
        card["@id"] = DATA + card_id
    return card


def _new_work(*cards: dict, label: str = "Nested Work") -> EntityData:
    """A new Work, as the editor sends it: no ``@id``."""
    data = {
        "@context": _CONTEXT,
        "@type": ["mm:MusicEntity", "lrmoo:F1_Work"],
        "rdfs:label": {"@value": label, "@language": "en"},
    }
    if cards:
        data["core:hasAgentRole"] = list(cards)
    return EntityData(shapeId=WORK_SHAPE, data=data, originalTriples=None)


def _minted(iri: str, name: str) -> bool:
    """Whether ``iri`` is a backend-minted ``<DATA><name>_<12hex>``."""
    return re.fullmatch(rf"{re.escape(DATA)}{name}_[0-9a-f]{{12}}", iri) is not None


class _GraphStore:
    """Store double backed by a real rdflib graph, so updates actually run.

    Creates need it too: naming a node checks the store for the IRI, and the
    records a payload references must be there for validation.
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

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        """Run a SELECT; rows map each variable to its value, as the real store does."""
        result = self.g.query(sparql)
        names = [str(v) for v in result.vars]
        return [
            {
                name: (None if value is None else str(value))
                for name, value in zip(names, row, strict=True)
            }
            for row in result
        ]

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

rfdb:person_x a core:Person ;
  rdfs:label "Composer X"@en .

rfdb:role_composer a core:Role ;
  rdfs:label "Composer"@en .
"""


def _work_without_agent_role() -> EntityData:
    """The stored work re-saved with its only connection removed in the form."""
    return EntityData(
        shapeId=WORK_SHAPE,
        data={
            "@context": _CONTEXT,
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


def _dep_request(store: _GraphStore) -> SimpleNamespace:
    """Request object carrying the real shape dependency graph."""
    extractor = SchemaExtractor(str(SCHEMA_PATH))
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                store=store,
                shape_dep_graph=build_shape_dep_graph(extractor),
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
    store = _GraphStore(_STORED_WORK)
    response = create_or_update_entity(_work_without_agent_role(), _dep_request(store))
    assert response.success is True

    work = URIRef(DATA + "stored_work")
    agent_role = URIRef(DATA + "stored_work_ar_0")

    assert (work, HAS_AGENT_ROLE, agent_role) not in store.g
    assert list(store.g.triples((agent_role, None, None))) == []
    # Standalone entities are untouched.
    assert (URIRef(DATA + "person_y"), RDF.type, URIRef(CORE + "Person")) in store.g
    assert (URIRef(DATA + "role_librettist"), RDF.type, URIRef(CORE + "Role")) in store.g


def test_linking_another_records_card_is_refused() -> None:
    """A card belongs to one record: sending another record's card IRI is a 409.

    Accepted, the save would add a second person and role to the other record's
    connection and share it between two records.
    """
    store = _GraphStore(
        _STORED_WORK
        + """
rfdb:other_work a mm:MusicEntity, lrmoo:F1_Work ;
  rdfs:label "Other Work"@en ;
  core:hasAgentRole rfdb:other_ar .
rfdb:other_ar a core:AgentRole ; core:hasAgent rfdb:person_x ; core:hasRole rfdb:role_composer .
"""
    )
    before = set(store.g)
    payload = _work_without_agent_role()
    payload.data["core:hasAgentRole"] = [
        _card("person_y", "role_librettist", "stored_work_ar_0"),
        _card("person_x", "role_librettist", "other_ar"),
    ]
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(payload, _dep_request(store))
    assert exc_info.value.status_code == 409
    assert set(store.g) == before


def test_editing_the_person_on_a_connection_replaces_the_old_one() -> None:
    """Re-pointing an existing connection at another person replaces the value.

    The bridge node keeps its IRI, so the store already holds ``hasAgent
    person_y`` while the payload asserts ``hasAgent person_z``. Both the
    validation merge and the write must treat the payload as authoritative for a
    bridge node it describes, or the save is rejected for exceeding
    ``sh:maxCount 1`` on a node the curator only edited.
    """
    store = _GraphStore(
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


class _FailingWriteStore(_GraphStore):
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
    store = _FailingWriteStore(_STORED_WORK)
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(_work_without_agent_role(), _dep_request(store))
    assert exc_info.value.status_code == 503

    work = URIRef(DATA + "stored_work")
    agent_role = URIRef(DATA + "stored_work_ar_0")
    assert (work, HAS_AGENT_ROLE, agent_role) in store.g
    assert (agent_role, RDF.type, AGENT_ROLE) in store.g
    assert (agent_role, HAS_AGENT, URIRef(DATA + "person_y")) in store.g
    assert (agent_role, HAS_ROLE, URIRef(DATA + "role_librettist")) in store.g


# ---------------------------------------------------------------------------
# Naming: the backend names every new record and card
# ---------------------------------------------------------------------------


def test_new_work_and_its_card_are_named_by_the_backend() -> None:
    """A record sent without ``@id`` and a card sent as a blank node get minted IRIs."""
    store = _GraphStore(_STORED_WORK)
    response = create_or_update_entity(
        _new_work(_card("person_x", "role_composer")), _dep_request(store)
    )
    assert response.success is True
    assert _minted(response.entityId, "MusicalWork")

    work = URIRef(response.entityId)
    (card,) = store.g.objects(work, HAS_AGENT_ROLE)
    assert _minted(str(card), "AgentRole")
    assert (card, RDF.type, AGENT_ROLE) in store.g
    assert (card, HAS_AGENT, URIRef(DATA + "person_x")) in store.g
    assert (card, HAS_ROLE, URIRef(DATA + "role_composer")) in store.g


def test_agent_role_without_type_is_rejected() -> None:
    """Dropping the explicit core:AgentRole @type makes the write fail: the parent
    link's sh:class/sh:node require a conforming AgentRole, which a node with no
    type cannot be."""
    card = _card("person_x", "role_composer")
    del card["@type"]
    response = create_or_update_entity(_new_work(card), _dep_request(_GraphStore(_STORED_WORK)))
    assert response.success is False


def test_create_with_an_id_is_refused() -> None:
    """``@id`` means update: an unknown one is a 404, and nothing is written."""
    store = _GraphStore(_STORED_WORK)
    before = set(store.g)
    payload = _new_work()
    payload.data["@id"] = DATA + "chosen_by_the_client"
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(payload, _dep_request(store))
    assert exc_info.value.status_code == 404
    assert set(store.g) == before


def test_minted_iri_already_in_use_is_redrawn() -> None:
    """A minted IRI the store already uses is drawn again, never merged into."""
    taken = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    fresh = uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
    store = _GraphStore(
        _STORED_WORK
        + """
rfdb:MusicalWork_aaaaaaaaaaaa a mm:MusicEntity, lrmoo:F1_Work ; rdfs:label "First"@en .
"""
    )
    with mock.patch("core.node_ids.uuid.uuid4", side_effect=[taken, fresh]):
        response = create_or_update_entity(_new_work(label="Second"), _dep_request(store))
    assert response.success is True
    assert response.entityId == DATA + "MusicalWork_bbbbbbbbbbbb"
    labels = set(store.g.objects(URIRef(DATA + "MusicalWork_aaaaaaaaaaaa"), RDFS.label))
    assert labels == {Literal("First", lang="en")}


# ---------------------------------------------------------------------------
# Editing cards: the payload is the whole list
# ---------------------------------------------------------------------------


def test_adding_a_card_next_to_an_existing_one_keeps_its_iri() -> None:
    """The kept card keeps its IRI and values; the new one is named and linked."""
    store = _GraphStore(_STORED_WORK)
    payload = _work_without_agent_role()
    payload.data["core:hasAgentRole"] = [
        _card("person_y", "role_librettist", "stored_work_ar_0"),
        _card("person_x", "role_composer"),
    ]
    response = create_or_update_entity(payload, _dep_request(store))
    assert response.success is True

    work = URIRef(DATA + "stored_work")
    kept = URIRef(DATA + "stored_work_ar_0")
    cards = set(store.g.objects(work, HAS_AGENT_ROLE))
    (new,) = cards - {kept}
    assert kept in cards
    assert _minted(str(new), "AgentRole")
    assert set(store.g.objects(kept, HAS_AGENT)) == {URIRef(DATA + "person_y")}
    assert set(store.g.objects(new, HAS_ROLE)) == {URIRef(DATA + "role_composer")}


def test_changed_card_replaces_its_values_even_when_not_listed_as_changed() -> None:
    """The payload's card is authoritative whatever ``originalTriples`` says.

    Here the client lists only the label as changed, yet changes the role on a
    card: the card must end with the new role only, not both.
    """
    store = _GraphStore(_STORED_WORK)
    payload = _work_without_agent_role()
    payload.data["rdfs:label"] = {"@value": "Renamed Work", "@language": "en"}
    payload.data["core:hasAgentRole"] = [_card("person_y", "role_composer", "stored_work_ar_0")]
    payload.originalTriples = [
        TripleObject(
            predicate=str(RDFS.label), object="Stored Work", objectType="literal", language="en"
        )
    ]
    response = create_or_update_entity(payload, _dep_request(store))
    assert response.success is True
    card = URIRef(DATA + "stored_work_ar_0")
    assert set(store.g.objects(card, HAS_ROLE)) == {URIRef(DATA + "role_composer")}


def test_changed_field_replaces_its_value_even_when_not_listed_as_changed() -> None:
    """A field the payload carries replaces the stored value, listed or not.

    Here the client lists nothing as changed, yet renames the Work: it must end
    with the new label only, not both.
    """
    store = _GraphStore(_STORED_WORK)
    payload = _work_without_agent_role()
    payload.data["rdfs:label"] = {"@value": "Renamed Work", "@language": "en"}
    payload.originalTriples = []
    response = create_or_update_entity(payload, _dep_request(store))
    assert response.success is True
    labels = set(store.g.objects(URIRef(DATA + "stored_work"), RDFS.label))
    assert labels == {Literal("Renamed Work", lang="en")}


def test_field_left_out_and_not_listed_is_kept() -> None:
    """A field the payload does not carry is untouched unless listed as emptied."""
    store = _GraphStore(
        _STORED_WORK
        + '\nrfdb:stored_work <http://www.w3.org/2004/02/skos/core#altLabel> "Alt"@en .\n'
    )
    payload = _work_without_agent_role()
    payload.originalTriples = []
    response = create_or_update_entity(payload, _dep_request(store))
    assert response.success is True
    alt = URIRef("http://www.w3.org/2004/02/skos/core#altLabel")
    assert set(store.g.objects(URIRef(DATA + "stored_work"), alt)) == {Literal("Alt", lang="en")}


@pytest.mark.parametrize(
    "card_iri",
    [
        DATA + "person_x",  # a Person record
        "https://glottolog.org/resource/languoid/id/russ1263",  # reference data
        DATA + "AgentRole_000000000000",  # an IRI nobody minted
    ],
)
def test_card_that_is_not_this_records_is_refused(card_iri: str) -> None:
    """A card IRI must be one this record links already; nothing is written otherwise."""
    store = _GraphStore(_STORED_WORK)
    before = set(store.g)
    payload = _work_without_agent_role()
    card = _card("person_y", "role_composer")
    card["@id"] = card_iri
    payload.data["core:hasAgentRole"] = [card]
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(payload, _dep_request(store))
    assert exc_info.value.status_code == 409
    assert set(store.g) == before


def test_existing_card_sent_without_its_values_is_refused() -> None:
    """A card is replaced by what is sent, so a bare reference would empty it."""
    store = _GraphStore(_STORED_WORK)
    payload = _work_without_agent_role()
    payload.data["core:hasAgentRole"] = [{"@id": DATA + "stored_work_ar_0"}]
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(payload, _dep_request(store))
    assert exc_info.value.status_code == 422


def test_describing_a_referenced_record_is_refused() -> None:
    """References are bare ``@id``s: a payload may not write onto the Person it cites."""
    store = _GraphStore(_STORED_WORK)
    before = set(store.g)
    card = _card("person_x", "role_composer")
    card["core:hasAgent"] = {
        "@id": DATA + "person_x",
        "rdfs:label": {"@value": "Renamed by a Work", "@language": "en"},
    }
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(_new_work(card), _dep_request(store))
    assert exc_info.value.status_code == 422
    assert set(store.g) == before


def test_blank_node_outside_a_card_is_refused() -> None:
    """Only a new card may be a blank node; any other would be stored anonymous."""
    payload = _new_work()
    payload.data["rdfs:seeAlso"] = {"rdfs:label": "anonymous"}
    with pytest.raises(HTTPException) as exc_info:
        create_or_update_entity(payload, _dep_request(_GraphStore(_STORED_WORK)))
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Deleting: cards go with their record; a record others need stays
# ---------------------------------------------------------------------------


def _delete(store: _GraphStore, iri: str) -> None:
    delete_entity(_dep_request(store), entity_id=DATA + iri, shapeId="")


def test_delete_of_a_missing_record_is_a_404() -> None:
    """Deleting an IRI the store does not describe is reported, not silently accepted."""
    store = _GraphStore(_STORED_WORK)
    before = set(store.g)
    with pytest.raises(HTTPException) as exc_info:
        _delete(store, "never_created")
    assert exc_info.value.status_code == 404
    assert set(store.g) == before


def test_delete_removes_the_records_cards() -> None:
    """The record's cards are deleted with it; the people and roles they cite stay."""
    store = _GraphStore(_STORED_WORK)
    _delete(store, "stored_work")
    assert list(store.g.triples((URIRef(DATA + "stored_work"), None, None))) == []
    assert list(store.g.triples((URIRef(DATA + "stored_work_ar_0"), None, None))) == []
    assert (URIRef(DATA + "person_y"), RDF.type, URIRef(CORE + "Person")) in store.g


def test_delete_is_refused_while_a_card_cites_the_record() -> None:
    """A Person used in a Work's card cannot go: the 409 names the Work, not the card."""
    store = _GraphStore(_STORED_WORK)
    before = set(store.g)
    with pytest.raises(HTTPException) as exc_info:
        _delete(store, "person_y")
    assert exc_info.value.status_code == 409
    assert "Stored Work" in exc_info.value.detail
    assert f"<{DATA}stored_work>" in exc_info.value.detail
    assert "stored_work_ar_0" not in exc_info.value.detail
    assert set(store.g) == before


def test_delete_is_refused_while_a_record_links_it_directly() -> None:
    """A direct link counts too, for example an Expression pointing at its Work."""
    store = _GraphStore(
        _STORED_WORK
        + f"""
rfdb:expression <{P148I}> rfdb:stored_work ; rdfs:label "An Expression"@en .
"""
    )
    with pytest.raises(HTTPException) as exc_info:
        _delete(store, "stored_work")
    assert exc_info.value.status_code == 409
    assert "An Expression" in exc_info.value.detail
    assert (URIRef(DATA + "stored_work_ar_0"), RDF.type, AGENT_ROLE) in store.g
