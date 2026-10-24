"""Data read routes: list entities by shape, count them, fetch one by IRI.

The write side (entity create and delete, digital-copy reconciliation, SHACL
merge planning) belongs to curator-backend, along with everything it needs.

The only shared helper these three routes use is the IRI guard in
``rfdb_core.iri``.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from rdflib import Literal, URIRef

from rfdb_core.iri import iri_error
from rfdb_core.models_data import DataListResponse

router = APIRouter()


def _validate_iri(entity_id: str) -> None:
    """Reject non-http(s) or unsafe IRIs before interpolating them in SPARQL.

    The rule itself lives in ``rfdb_core.iri`` (both services enforce it); this
    is only the HTTP mapping, which is the service's concern.
    """
    if reason := iri_error(entity_id):
        raise HTTPException(status_code=400, detail=reason)


@router.get("/entities", response_model=DataListResponse)
def list_data(
    request: Request,
    shapeId: str = Query(..., description="Full URI of the SHACL NodeShape"),
    q: str = Query("", description="Filter by label or ID prefix"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Return a paginated list of entities that conform to a given SHACL shape.

    Entities are retrieved from the configured named graph and ordered by IRI.
    When ``q`` is provided, results are filtered by case-insensitive regex over
    both the primary ``rdfs:label`` and the entity IRI.

    Args:
        shapeId: Full URI of a NodeShape defined in ``schema/rfdb_schema.ttl``.
        q: Optional free-text filter applied to label and IRI.
        limit: Maximum number of items to return (1–500, default 50).
        offset: Number of items to skip for pagination (default 0).

    Returns:
        A ``DataListResponse`` with ``total`` (unpaged count) and ``items``
        (the current page).  Each item carries ``id``, ``label`` and
        ``labelLang``.

    Raises:
        404: When ``shapeId`` is not found in the schema.
    """
    extractor = request.app.state.schema_extractor
    shape = extractor.get_shape(shapeId)
    if shape is None:
        raise HTTPException(status_code=404, detail=f"Shape '{shapeId}' not found")

    target_class = shape.get("targetClassUri")
    if not target_class:
        return DataListResponse(shapeId=shapeId, total=0, items=[])

    escaped_q = q.replace("\\", "\\\\").replace('"', '\\"')
    filter_clause = (
        f'FILTER(regex(COALESCE(str(?label_raw), ""), "{escaped_q}", "i") '
        f'|| regex(str(?id), "{escaped_q}", "i"))'
        if q
        else ""
    )

    store = request.app.state.store

    # One flat SELECT, no subquery: FROM does not propagate into subqueries
    # (SPARQL 1.1 §8.2), so the OPTIONAL label lookup has to sit in the same
    # WHERE as the type pattern. GROUP BY ?id with SAMPLE then collapses an
    # entity with several labels to a single row.
    sparql = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?id (SAMPLE(?label_raw) AS ?label) (SAMPLE(?lang) AS ?labelLang)
        {store.from_clause()}
        WHERE {{
            ?id a <{target_class}> .
            OPTIONAL {{
                ?id rdfs:label ?label_raw .
                BIND(LANG(?label_raw) AS ?lang)
            }}
            {filter_clause}
        }}
        GROUP BY ?id
        ORDER BY ?id
        LIMIT {limit}
        OFFSET {offset}
    """

    try:
        rows = store.query(sparql)
    except Exception:
        rows = []

    items = [
        {
            "id": r["id"],
            "label": r.get("label"),
            "labelLang": r.get("labelLang") or None,
        }
        for r in rows
        if r.get("id")
    ]

    count_sparql = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT (COUNT(DISTINCT ?id) AS ?total)
        {store.from_clause()}
        WHERE {{
            ?id a <{target_class}> .
            OPTIONAL {{ ?id rdfs:label ?label_raw . }}
            {filter_clause}
        }}
    """

    try:
        count_rows = store.query(count_sparql)
        total = int(count_rows[0]["total"]) if count_rows else 0
    except Exception:
        total = len(items)

    return DataListResponse(shapeId=shapeId, total=total, items=items)


@router.get("/entities/counts")
def count_data_by_shape(request: Request):
    """Return the number of stored entities for every SHACL shape.

    Iterates all shapes from the schema, runs a ``COUNT(DISTINCT ?id)`` query
    for each one's ``sh:targetClass``, and returns a single dict.  Shapes
    without a ``targetClass`` are included with count 0.  Query errors for
    any individual shape are silently treated as 0 so a single broken shape
    does not block the sidebar from rendering.

    Returns:
        ``{"counts": {<shapeUri>: <int>, ...}}``
    """
    extractor = request.app.state.schema_extractor
    store = request.app.state.store

    counts: dict[str, int] = {}
    for shape in extractor.get_all_shapes():
        shape_id = shape.get("id")
        target_class = shape.get("targetClassUri")

        if not shape_id:
            continue

        if not target_class:
            counts[shape_id] = 0
            continue

        sparql = f"""
            SELECT (COUNT(DISTINCT ?id) AS ?total)
            {store.from_clause()}
            WHERE {{
                ?id a <{target_class}> .
            }}
        """

        try:
            rows = store.query(sparql)
            counts[shape_id] = int(rows[0]["total"]) if rows else 0
        except Exception:
            counts[shape_id] = 0

    return {"counts": counts}


@router.get("/entities/get")
def get_entity(
    request: Request,
    entity_id: str = Query(..., alias="id", description="Full IRI of the entity"),
):
    """Fetch all triples for a single entity by IRI.

    The IRI travels as ``?id=``, matching ``/graph/node``, and **never** as a path
    segment. Two reasons:

    1. **No route may shadow a sibling.** A path parameter here would have to be
       ``{iri:path}`` — greedy, because an encoded IRI's ``%2F`` is decoded before
       matching — and a greedy parameter directly under ``/entities/`` would
       silently swallow ``/entities/search`` and ``/entities/counts`` unless every
       literal route were registered first, an invariant invisible at the call
       site.
    2. **No double-encoding trap.** A path-encoded IRI has to be encoded twice to
       survive, and a bare local name fails the unsafe-IRI guard with a 400 that
       looks like a data problem.

    ``get`` is a verb in a URL, which is deliberate: this is the *operational*
    surface, where the HTTP method is not enough to disambiguate a lookup from a
    collection. The resource-oriented address for the same entity already exists as
    ``GET /rdf/data/{local_name}``, which is where RESTful identity belongs.

    Issues a SPARQL CONSTRUCT scoped to the configured named graph and returns
    every ``(predicate, object)`` pair as a JSON array.  Literal objects carry
    ``datatype`` and ``language`` metadata so the frontend form can reconstruct
    the original RDF values faithfully.

    Args:
        entity_id: Full IRI of the entity, from the ``?id=`` query parameter.

    Returns:
        ``{"id": <iri>, "triples": [{predicate, object, objectType,
        datatype, language}, ...]}`` sorted by predicate then object.

    Raises:
        404: When no triples are found for ``entity_id``.
        503: When Oxigraph is unreachable.
    """
    _validate_iri(entity_id)
    store = request.app.state.store

    sparql = f"""
        CONSTRUCT {{
            <{entity_id}> ?predicate ?object .
        }}
        {store.from_clause()}
        WHERE {{
            <{entity_id}> ?predicate ?object .
        }}
    """

    try:
        graph = store.construct(sparql)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Store unavailable") from exc

    if len(graph) == 0:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    triples = []
    subject = URIRef(entity_id)

    for _, predicate, obj in graph.triples((subject, None, None)):
        triples.append(
            {
                "predicate": str(predicate),
                "object": str(obj),
                "objectType": "literal" if isinstance(obj, Literal) else "iri",
                "datatype": str(obj.datatype)
                if isinstance(obj, Literal) and obj.datatype
                else None,
                "language": obj.language if isinstance(obj, Literal) else None,
            }
        )

    triples.sort(key=lambda t: (t["predicate"], t["object"]))
    return {"id": entity_id, "triples": triples}
