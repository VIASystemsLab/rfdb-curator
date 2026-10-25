"""Pydantic models for the data-entry API request/response contracts.

Kept in one file so the shape of every payload is easy to audit.

Write-path only; the record-list read models live in ``rfdb_core.models_data``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TripleObject(BaseModel):
    predicate: str
    object: str
    objectType: str
    datatype: str | None = None
    language: str | None = None


class EntityData(BaseModel):
    """Input model for `POST /api/v1/curator/entities` and `POST /api/v1/curator/validate`.

    Attributes:
        shapeId: Full URI of the SHACL NodeShape that constrains this entity
            (e.g. `"https://rosfeatr.eu/rdf/schema/PlaceShape"`).
        data: Raw JSON-LD object.  Must carry a `@context` and `@type`. No `@id`
            creates a record, named by the backend (`core.node_ids`); an `@id`
            updates that record, which must exist.
        originalTriples: List of TripleObject for predicates the user edited (update only).
    """

    shapeId: str
    data: dict[str, Any]
    originalTriples: list[TripleObject] | None = None


class ValidationResult(BaseModel):
    """Mirrors a SHACL `sh:ValidationReport`.  Returned by both the dry-run
    validate endpoint and embedded inside `DataCreateResponse` on every write.
    """

    conforms: bool = True
    violations: list[dict[str, Any]] = Field(default_factory=list)


class DataCreateResponse(BaseModel):
    """Response for `POST /api/v1/curator/entities`.

    When `success` is False the entity was **not** stored; the frontend shows
    the `validationReport` violations and lets the user correct the form.
    """

    success: bool
    entityId: str | None = None
    validationReport: ValidationResult
