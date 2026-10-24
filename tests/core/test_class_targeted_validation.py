"""Class-targeted SHACL validation: constraints fire only for nodes that declare
the shape's target class.

This pins the behaviour documented in docs/data-model.md ("Class-targeted
validation requires explicit @type"): a shape with ``sh:targetClass`` only
selects focus nodes of that class, so a payload that omits its ``@type`` silently
skips the shape's constraints instead of failing. The tests below pin both
sides, so a schema or validator change that alters either one fails here.
"""

from __future__ import annotations

from pathlib import Path

from pyshacl import validate
from rdflib import Graph

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "rfdb_schema.ttl"

_PREFIXES = """
@prefix core: <https://w3id.org/polifonia/ontology/core/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rfdb: <https://rosfeatr.eu/rdf/data/> .
"""


def _conforms(body: str) -> bool:
    """Validate a Turtle snippet against the project schema; return conformance."""
    conforms, _, _ = validate(
        data_graph=Graph().parse(data=_PREFIXES + body, format="turtle"),
        shacl_graph=Graph().parse(str(SCHEMA_PATH), format="turtle"),
        inference="rdfs",
        advanced=True,
    )
    return conforms


def test_typed_place_missing_required_label_is_rejected() -> None:
    """A node typed core:Place is a PlaceShape focus node, so a missing required
    rdfs:label is a violation."""
    assert not _conforms("rfdb:P1 a core:Place .\n")


def test_typed_place_with_label_conforms() -> None:
    """The same node conforms once the required label is present."""
    assert _conforms('rfdb:P1 a core:Place ; rdfs:label "Verona" .\n')


def test_untyped_node_skips_place_constraints() -> None:
    """Without the core:Place type the node is never targeted, so the same
    missing-label graph conforms — exactly why explicit @type is mandatory."""
    assert _conforms('rfdb:P1 rdfs:label "Verona" .\n')
