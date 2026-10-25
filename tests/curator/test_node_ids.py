"""Names for new records and helper nodes: ``<data base><ShapeName>_<12 hex>``, never reused."""

from __future__ import annotations

import re

import pytest

from core.node_ids import MintError, mint_iri, shape_name

BASE = "https://example.org/rdf/data/"


@pytest.mark.parametrize(
    ("shape_id", "name"),
    [
        ("https://example.org/rdf/schema/AgentRoleShape", "AgentRole"),
        ("https://example.org/rdf/schema#PlaceShape", "Place"),
    ],
)
def test_shape_name_drops_the_namespace_and_shape(shape_id: str, name: str) -> None:
    """Slash and hash namespaces both give the bare name."""
    assert shape_name(shape_id) == name


def test_minted_iri_is_under_the_base_with_12_hex() -> None:
    """The IRI is the data base, the shape name and 12 lowercase hex digits."""
    iri = mint_iri(BASE, "https://example.org/rdf/schema/PlaceShape", lambda _iri: False)
    assert re.fullmatch(rf"{re.escape(BASE)}Place_[0-9a-f]{{12}}", iri)


def test_a_taken_iri_is_drawn_again() -> None:
    """An IRI ``is_taken`` reports as used is never returned."""
    seen: list[str] = []

    def taken_once(iri: str) -> bool:
        seen.append(iri)
        return len(seen) == 1

    iri = mint_iri(BASE, "https://example.org/rdf/schema/PlaceShape", taken_once)
    assert iri == seen[1] != seen[0]


def test_every_draw_taken_raises() -> None:
    """A check that rejects everything stops after a few draws instead of looping."""
    with pytest.raises(MintError):
        mint_iri(BASE, "https://example.org/rdf/schema/PlaceShape", lambda _iri: True)
