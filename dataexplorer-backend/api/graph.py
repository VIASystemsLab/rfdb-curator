"""Schema-aware graph traversal for the read-only Explorer app.

The Explorer visualizes lineage and relationships by walking the RDF graph one
node at a time. Rather than following every triple blindly, it uses the SHACL
schema as the map of what counts as a *relationship*: every shape property that
links to another entity (``sh:node`` / ``sh:class``) contributes a "relation
predicate". ``GET /api/v1/dataexplorer/graph/node?id=<iri>`` then returns, for one node:

  - its own label, RDF types, literal fields, and external same-as/see-also links;
  - outbound relation edges (``<id> <relPred> ?neighbor``); and
  - inbound relation edges (``?neighbor <relPred> <id>``),

each neighbor carrying its label and types so the client can render and expand
it. Traversing only schema-declared relation predicates keeps the graph to
meaningful, typed lineage edges and works in both directions — up the WEMI chain
from a Source and out to the operas, people, and performances around it.

Reads are scoped to the configured named graph via ``TripleStore.from_clause``;
raw full IRIs are returned and compacted to CURIEs on the client (same contract
as the rest of the API).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS

from rfdb_core.external_ids import external_id_url
from rfdb_core.iri import iri_error

router = APIRouter()

_RDF_TYPE = str(RDF.type)
_RDFS_NS = str(RDFS)
_RDFS_LABEL = str(RDFS.label)


def _validate_iri(iri: str) -> None:
    """Reject non-http(s) or unsafe IRIs before interpolating them into SPARQL.

    The rule itself lives in ``rfdb_core.iri`` (both services enforce it); this
    is only the HTTP mapping, which is the service's concern.
    """
    if reason := iri_error(iri):
        raise HTTPException(status_code=400, detail=reason)


def relation_predicates(extractor) -> list[str]:
    """Return every predicate the schema treats as an entity-to-entity relation.

    A shape property is a relation when it links to another entity — i.e. it
    declares ``sh:node`` (``nestedShape``) or ``sh:class`` (``nodeClass``).
    External-authority IRIs (``owl:sameAs``) declare neither, so they are
    excluded and surfaced as ``externalLinks`` instead. Identifiers stored as
    text (VIAF, RISM) are literals, with a ``url`` when ``EXTERNAL_ID_LINKS`` has
    a template for their predicate.
    """
    preds: set[str] = set()
    for shape in extractor.get_all_shapes():
        for prop in shape.get("properties", []):
            if prop.get("nestedShape") or prop.get("nodeClass"):
                pred = prop.get("pathUri")
                if pred:
                    preds.add(pred)
    return sorted(preds)


def preferred_label(labels: list[Literal]) -> str | None:
    """Pick the best label: English, then untagged, then any, else None.

    Public because ``api.resource`` titles its HTML page with it. One rule for
    "which of several labels does a human see", not two that agree today.
    """
    if not labels:
        return None
    en = next((str(o) for o in labels if (o.language or "").lower() == "en"), None)
    if en is not None:
        return en
    plain = next((str(o) for o in labels if not o.language), None)
    return plain if plain is not None else str(labels[0])


def _neighbor_edges(
    store, iri: str, rel_preds: list[str], inbound: bool, limit: int
) -> tuple[list[dict], bool]:
    """Query one direction of relation edges with each neighbor's label and types.

    A single flat query per direction (no subquery, so ``FROM`` scoping applies to
    the OPTIONAL label/type lookups too). ``GROUP BY`` + ``SAMPLE`` collapses a
    neighbor that carries several labels/types to one row.

    Capped at ``limit`` neighbors per direction so a high-degree hub cannot return
    (or render) an unbounded fan-out. One extra row is fetched to detect the cap:
    the returned ``truncated`` flag is ``True`` when more neighbors exist than were
    returned, so the caller can surface "N more" rather than silently dropping them.
    """
    values = " ".join(f"<{p}>" for p in rel_preds)
    var = "?s" if inbound else "?o"
    triple = f"{var} ?p <{iri}>" if inbound else f"<{iri}> ?p {var}"
    sparql = f"""
        PREFIX rdfs: <{_RDFS_NS}>
        SELECT ?p {var} (SAMPLE(?l) AS ?label)
               (GROUP_CONCAT(DISTINCT STR(?t); SEPARATOR=" ") AS ?types)
        {store.from_clause()}
        WHERE {{
            VALUES ?p {{ {values} }}
            {triple} .
            FILTER(isIRI({var}))
            OPTIONAL {{ {var} rdfs:label ?l }}
            OPTIONAL {{ {var} a ?t }}
        }}
        GROUP BY ?p {var}
        ORDER BY ?p {var}
        LIMIT {limit + 1}
    """
    rows = store.query(sparql)
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    key = "s" if inbound else "o"
    edges = []
    for r in rows:
        neighbor_id = r.get(key)
        if not neighbor_id:
            continue
        types = [t for t in (r.get("types") or "").split(" ") if t]
        edges.append(
            {
                "direction": "in" if inbound else "out",
                "predicate": r["p"],
                "neighbor": {"id": neighbor_id, "label": r.get("label"), "types": sorted(types)},
            }
        )
    return edges, truncated


@router.get("/graph/node")
def get_node(
    request: Request,
    id: str = Query(..., description="Full IRI of the entity"),
    limit: int = Query(200, ge=1, le=2000, description="Max neighbors returned per direction"),
):
    """Return one node's own data plus its schema-defined relation edges.

    Response shape::

        {
          "id": "<iri>",
          "label": "…" | null,
          "types": ["<classUri>", …],
          "literals": [{"predicate","value","datatype","language","url"}, …],
          "externalLinks": [{"predicate","target"}, …],
          "edges": [{"direction":"out"|"in","predicate","neighbor":{id,label,types}}, …],
          "truncated": bool  # true when a direction hit `limit` and more neighbors exist
        }

    Raises:
        400: ``id`` is not a valid http(s) IRI.
        404: no triples and no inbound edges for ``id``.
        503: Oxigraph is unreachable.
    """
    _validate_iri(id)
    extractor = request.app.state.schema_extractor
    store = request.app.state.store
    rel_preds = relation_predicates(extractor)
    link_templates = extractor.external_id_links

    try:
        self_graph = store.construct(
            f"CONSTRUCT {{ <{id}> ?p ?o }} {store.from_clause()} WHERE {{ <{id}> ?p ?o }}"
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Store unavailable") from exc

    subject = URIRef(id)
    rel_set = set(rel_preds)
    types: list[str] = []
    labels: list[Literal] = []
    literals: list[dict] = []
    external: list[dict] = []
    for _, predicate, obj in self_graph.triples((subject, None, None)):
        pred = str(predicate)
        if pred == _RDF_TYPE:
            types.append(str(obj))
        elif isinstance(obj, Literal):
            if pred == _RDFS_LABEL:
                labels.append(obj)
            else:
                literals.append(
                    {
                        "predicate": pred,
                        "value": str(obj),
                        "datatype": str(obj.datatype) if obj.datatype else None,
                        "language": obj.language,
                        # Set for an identifier stored as text, e.g. a VIAF ID.
                        "url": (
                            external_id_url(link_templates[pred], str(obj))
                            if pred in link_templates
                            else None
                        ),
                    }
                )
        elif isinstance(obj, URIRef) and pred not in rel_set:
            external.append({"predicate": pred, "target": str(obj)})

    truncated = False
    try:
        edges = []
        if rel_preds:
            out_edges, out_trunc = _neighbor_edges(store, id, rel_preds, inbound=False, limit=limit)
            in_edges, in_trunc = _neighbor_edges(store, id, rel_preds, inbound=True, limit=limit)
            edges = out_edges + in_edges
            truncated = out_trunc or in_trunc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Store unavailable") from exc

    if len(self_graph) == 0 and not edges:
        raise HTTPException(status_code=404, detail=f"Entity '{id}' not found")

    edges.sort(key=lambda e: (e["direction"], e["predicate"], e["neighbor"]["id"]))
    literals.sort(key=lambda x: (x["predicate"], x["value"]))
    external.sort(key=lambda x: (x["predicate"], x["target"]))
    return {
        "id": id,
        "label": preferred_label(labels),
        "types": sorted(types),
        "literals": literals,
        "externalLinks": external,
        "edges": edges,
        "truncated": truncated,
    }
