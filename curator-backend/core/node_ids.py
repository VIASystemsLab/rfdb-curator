"""Names for the nodes the curator creates: records and their bridge nodes.

The backend is the only place a node is named. Clients send a new record without
``@id`` and a new bridge card as a blank node; both get
``<DATA_BASE_URI><ShapeName>_<12 hex>``, where ShapeName is the shape's local
name without "Shape"::

    shape_id = "https://rosfeatr.eu/rdf/schema/PlaceShape"
    result   = "https://rosfeatr.eu/rdf/data/Place_4f3a9d12c07e"
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

# Draws before giving up. With 12 random hex digits a single collision is already
# rare; five in a row means the check itself is broken.
_MAX_DRAWS = 5


class MintError(RuntimeError):
    """Every draw collided with an IRI already in use."""


def shape_name(shape_id: str) -> str:
    """The shape's local name without "Shape": ``…/schema/AgentRoleShape`` → ``AgentRole``."""
    return shape_id.split("#")[-1].split("/")[-1].replace("Shape", "")


def mint_iri(data_base: str, shape_id: str, is_taken: Callable[[str], bool]) -> str:
    """A new IRI for a node of ``shape_id`` under ``data_base``, unused by ``is_taken``.

    Raises:
        MintError: every draw was taken.
    """
    name = shape_name(shape_id)
    for _ in range(_MAX_DRAWS):
        iri = f"{data_base}{name}_{uuid.uuid4().hex[:12]}"
        if not is_taken(iri):
            return iri
    raise MintError(f"No unused IRI for {name} after {_MAX_DRAWS} draws")
