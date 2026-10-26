"""Entity autocomplete search route used by relation (EntitySearch) fields.

When a user types into a relation field (e.g., `core:hasPlace`) the frontend
calls `GET /api/v1/dataexplorer/entities/search?shape=PlaceShape&query=<text>` and renders
the results as dropdown options.  The search fires on an empty query too, so
the dropdown is pre-populated when the field receives focus.

SPARQL strategy: a flat `GROUP BY ?uri` with `SAMPLE` collapses entities that
carry several `rdfs:label` / `rdfs:comment` values to one row each, and a FILTER
applies the case-insensitive text match on the label, the IRI, or any of the
record's other names (the predicates in `SEARCH_LABEL_PREDICATES`). Each result
carries `uri`, `label`, and (when present) `comment` — the dropdown shows the
comment as a secondary line to help tell similarly-named entities apart.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from core.config import settings
from rfdb_core.sparql_text import regex_literal

logger = logging.getLogger(__name__)

router = APIRouter()


def _local_name(iri: str) -> str:
    """The part of an IRI after its last ``#`` or ``/``."""
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


@router.get("/entities/search")
def search_entities(
    request: Request,
    shape: str = Query(..., description="Shape label or full URI (e.g. PlaceShape)"),
    query: str = Query("", description="Partial label or IRI to match"),
    limit: int = Query(50, ge=1, le=100),
):
    """Search for entities that conform to a given SHACL shape.

    The `shape` parameter accepts either the full shape URI or its exact local
    name (e.g., `"PlaceShape"`) for convenience; anything else is a 404.  The target class is
    resolved from the shape metadata so the SPARQL query is always correct.

    Text matching is a case-insensitive, literal substring match (the text is
    escaped, so `(` or `.` match themselves) over the primary label, the
    record's other names (``SEARCH_LABEL_PREDICATES``, by default
    ``skos:altLabel``) and the entity URI, so users can paste a partial IRI
    directly into the field. A record found by another name is still listed
    under its primary label.
    An empty `query` string returns all entities up to `limit` (used for the
    initial dropdown population on field focus).
    """
    extractor = request.app.state.schema_extractor

    # Match the full URI or the whole local name, never a suffix: with a suffix
    # match "RoleShape" also hits "AgentRoleShape", and which one wins depends
    # on the order of the shapes in the schema.
    all_shapes = extractor.get_all_shapes()
    matched = next(
        (s for s in all_shapes if shape in (s["id"], _local_name(s["id"]))),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Shape '{shape}' not found")

    target_class = matched.get("targetClassUri")
    if not target_class:
        return []

    pattern = regex_literal(query)
    # Other names are matched in an EXISTS rather than joined in, so a record
    # with many names does not multiply the rows before GROUP BY.
    other_names = "|".join(f"<{p}>" for p in settings.search_label_predicates)
    other_names_clause = (
        f' || EXISTS {{ ?uri {other_names} ?name . FILTER(regex(str(?name), "{pattern}", "i")) }}'
        if other_names
        else ""
    )
    filter_clause = (
        f'FILTER(regex(COALESCE(str(?label_raw), ""), "{pattern}", "i") '
        f'|| regex(str(?uri), "{pattern}", "i"){other_names_clause})'
        if query
        else ""
    )

    # SPARQL notes:
    #  - FROM does not propagate into subqueries (SPARQL 1.1 §8.2), so the query
    #    stays flat and the OPTIONAL lookups run inside the FROM-scoped dataset.
    #  - GROUP BY ?uri + SAMPLE collapses entities that carry several labels or
    #    comments to one row each; ordering by the grouping key keeps Oxigraph
    #    happy (the frontend re-sorts by label anyway).
    store = request.app.state.store
    sparql = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?uri (SAMPLE(?label_raw) AS ?label) (SAMPLE(?comment_raw) AS ?comment)
        {store.from_clause()}
        WHERE {{
            ?uri a <{target_class}> .
            OPTIONAL {{ ?uri rdfs:label ?label_raw . }}
            OPTIONAL {{ ?uri rdfs:comment ?comment_raw . }}
            {filter_clause}
        }}
        GROUP BY ?uri
        ORDER BY ?uri
        LIMIT {limit}
    """

    # An unreachable store degrades to no suggestions, as the list routes do,
    # but never silently: the warning is what tells an operator why.
    try:
        rows = store.query(sparql)
    except Exception as exc:
        logger.warning("Entity search for shape %s failed: %s", matched["id"], exc)
        return []

    return [
        {"uri": r["uri"], "label": r.get("label", r["uri"]), "comment": r.get("comment")}
        for r in rows
        if r.get("uri")
    ]
