"""Response models for the record-list read contract.

The reader returns them from ``GET /api/v1/dataexplorer/entities`` as
``response_model``, so FastAPI enforces the shape. The curator frontend reads its
record list from that route too, so these are the only copy.

The write-path models (``EntityData``, ``DataCreateResponse``,
``ValidationResult``, ``TripleObject``) live in the curator: they describe
mutations, which only that service performs.
"""

from __future__ import annotations

from pydantic import BaseModel


class DataListItem(BaseModel):
    """A single row in the record list panel.

    `label` and `labelLang` come from a SPARQL SAMPLE over all `rdfs:label`
    values so the row always shows exactly one label even when the entity has
    multiple language variants.
    """

    id: str
    label: str | None = None
    labelLang: str | None = None
    updatedAt: str | None = None


class DataListResponse(BaseModel):
    """Paginated response for `GET /api/v1/dataexplorer/entities`.

    `total` is the full result count (not capped by `limit`) so the frontend
    can render accurate pagination controls.
    """

    shapeId: str
    total: int
    items: list[DataListItem]
