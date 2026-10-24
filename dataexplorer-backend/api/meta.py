"""Meta routes: schema-level information not tied to individual shapes or entities.

Currently exposes:
  GET /api/v1/dataexplorer/meta/schema   — the loaded schema's version (owl:versionInfo),
                                           the project name, the base IRIs records and
                                           shapes are named under, and the external-ID
                                           link templates.
  GET /api/v1/dataexplorer/meta/prefixes — CURIE prefix map declared by the schema (sh:declare).
  GET /api/v1/dataexplorer/meta/graphs   — active/named graphs, triple counts, config warnings.
  GET /api/v1/dataexplorer/meta/files    — digital-copy storage stats (staged/registered/orphans).

These back the read-only Data Context Panel (see TODO.md).
"""

import time

from fastapi import APIRouter, HTTPException, Request

from core.config import settings
from rfdb_core.file_storage import REGISTERED_PREFIX, STAGED_PREFIX, StorageError
from rfdb_core.files_state import collect_file_state, key_file_id

router = APIRouter()


@router.get("/meta/schema")
def get_schema_info(request: Request):
    """Return what the loaded schema says about itself, and the namespaces in use.

    The base IRIs are served as explicit values rather than looked up by a prefix
    name, so a deployment can declare them under any prefix. The curator frontend
    reads ``dataBaseUri`` to tell this deployment's records from external IRIs.
    ``projectName`` is the ``PROJECT_NAME`` setting, for the editor's header.
    ``externalIdLinks`` is the ``EXTERNAL_ID_LINKS`` map, predicate IRI → URL
    template, for clients that render identifiers stored as text.

    Returns:
        ``{"projectName": "…", "version": "2.2.2", "dataBaseUri": "https://…/data/",
        "schemaBaseUri": "https://…/schema/", "externalIdLinks": {…}}``.
        ``version`` is ``null`` when the
        schema states no ``owl:versionInfo``.
    """
    return {
        "projectName": settings.project_name,
        "version": request.app.state.schema_extractor.version,
        "dataBaseUri": settings.data_base_uri,
        "schemaBaseUri": settings.schema_base_uri,
        "externalIdLinks": settings.external_id_links,
    }


@router.get("/meta/prefixes")
def get_prefixes(request: Request):
    """Return the CURIE prefix→namespace map the schema declares.

    Read from the schema's ``sh:declare`` declarations at load time (see
    ``rfdb_core.prefixes``), **not** from the rdflib graph's namespaces, which also
    carry ~29 unrelated vocabularies rdflib pre-binds into every ``Graph``.

    Returns:
        ``{"prefixes": {"cidoc": "http://…", "xsd": "http://…", …}}``

    The frontend consumes this at startup to hydrate its prefix map for IRI
    compaction and the Data Context Panel.
    """
    return {"prefixes": dict(request.app.state.schema_extractor.prefixes)}


def _count(rows: list[dict], key: str = "count") -> int:
    """Cast a SPARQL ``COUNT`` cell (a string, or None/missing) to int."""
    if not rows:
        return 0
    return int(rows[0].get(key) or 0)


@router.get("/meta/graphs")
def get_graphs(request: Request):
    """Return the runtime graph context: active graph, named graphs + triple
    counts, a store-wide total, and lightweight configuration warnings.

    Read-only and store-wide. Unlike normal route handlers, the two SPARQL
    queries run **unscoped** (no ``from_clause()``) so the panel can see every
    named graph, not just the configured data graph.

    Returns:
        ``{"activeGraph": str | None,
           "graphs": [{"uri", "count", "subjects", "objects", "literals", "active"}],
           "totalTriples": int, "totalSubjects": int, "totalObjects": int,
           "totalLiterals": int, "warnings": [str]}``

    Per-graph ``subjects``/``objects``/``literals`` are distinct-term counts within
    that graph. The ``total*`` distinct fields are counted once store-wide, so they
    can be smaller than the sum of the per-graph columns (a term can recur).

    Raises:
        HTTPException 503: when Oxigraph is unreachable (matches ``api/data.py``).
    """
    active_graph = settings.data_graph_uri or None
    store = request.app.state.store

    try:
        # Per-named-graph triple count + distinct subjects/objects, one grouped query.
        count_rows = store.query(
            "SELECT ?g (COUNT(*) AS ?count) (COUNT(DISTINCT ?s) AS ?subjects) "
            "(COUNT(DISTINCT ?o) AS ?objects) "
            "WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?g"
        )
        # Distinct literals per graph needs a FILTER, so a second grouped query.
        literal_rows = store.query(
            "SELECT ?g (COUNT(DISTINCT ?o) AS ?literals) "
            "WHERE { GRAPH ?g { ?s ?p ?o } FILTER(isLiteral(?o)) } GROUP BY ?g"
        )
        default_count = _count(store.query("SELECT (COUNT(*) AS ?count) WHERE { ?s ?p ?o }"))
        # Store-wide distinct totals for the footer. The UNION spans the default
        # graph and every named graph, deduplicating terms shared across graphs —
        # so these can be smaller than the sum of the per-graph columns.
        total_distinct_rows = store.query(
            "SELECT (COUNT(DISTINCT ?s) AS ?subjects) (COUNT(DISTINCT ?o) AS ?objects) "
            "WHERE { { ?s ?p ?o } UNION { GRAPH ?g { ?s ?p ?o } } }"
        )
        total_literal_rows = store.query(
            "SELECT (COUNT(DISTINCT ?o) AS ?literals) "
            "WHERE { { ?s ?p ?o } UNION { GRAPH ?g { ?s ?p ?o } } FILTER(isLiteral(?o)) }"
        )
    except Exception as exc:  # store unreachable / malformed response
        raise HTTPException(status_code=503, detail="Store unavailable") from exc

    literals_by_graph = {
        row["g"]: int(row.get("literals") or 0) for row in literal_rows if row.get("g")
    }
    graphs = sorted(
        (
            {
                "uri": row["g"],
                "count": int(row.get("count") or 0),
                "subjects": int(row.get("subjects") or 0),
                "objects": int(row.get("objects") or 0),
                "literals": literals_by_graph.get(row["g"], 0),
                "active": row["g"] == active_graph,
            }
            for row in count_rows
            if row.get("g")
        ),
        key=lambda item: item["uri"],
    )
    total_triples = sum(g["count"] for g in graphs) + default_count

    # Lightweight, advisory-only consistency hints (no severity/codes).
    warnings: list[str] = []
    if active_graph is None:
        warnings.append("No DATA_GRAPH_URI configured; reads and writes target the default graph.")
    else:
        active_row = next((g for g in graphs if g["uri"] == active_graph), None)
        if active_row is None or active_row["count"] == 0:
            warnings.append(f"Active data graph <{active_graph}> is empty or absent in the store.")
    if default_count > 0:
        scope = f"<{active_graph}>" if active_graph else "the default graph"
        warnings.append(
            f"{default_count} triples live in the default graph and are invisible to "
            f"the editor (all reads are scoped to {scope})."
        )

    return {
        "activeGraph": active_graph,
        "graphs": graphs,
        "totalTriples": total_triples,
        "totalSubjects": _count(total_distinct_rows, "subjects"),
        "totalObjects": _count(total_distinct_rows, "objects"),
        "totalLiterals": _count(total_literal_rows, "literals"),
        "warnings": warnings,
    }


@router.get("/meta/files")
def get_file_stats(request: Request):
    """Return digital-copy storage stats for the Data Context Panel.

    Mirrors the reconciler's view (``curator-backend/scripts/cleanup_files.py``):
    RDF is the source of truth, storage is compared against it. Orphan counts > 0
    signal it is time to run the cleanup script.

    Returns:
        ``{"configured": bool,
           "staged":     {"count", "bytes", "oldestAgeS"},
           "registered": {"count", "bytes"},
           "linkedNodes": int,          # file nodes reachable from a parent
           "orphanedNodes": int,        # typed but unlinked (entity deleted)
           "unreferencedStaged": int,   # abandoned uploads awaiting TTL
           "unreferencedRegistered": int}``

    ``configured: false`` (no ``S3_ENDPOINT`` set) returns zeroed stats
    instead of an error so the panel renders in storage-less deployments.
    """
    if not settings.s3_endpoint:
        empty = {"count": 0, "bytes": 0}
        return {
            "configured": False,
            "staged": {**empty, "oldestAgeS": None},
            "registered": empty,
            "linkedNodes": 0,
            "orphanedNodes": 0,
            "unreferencedStaged": 0,
            "unreferencedRegistered": 0,
        }

    try:
        state = collect_file_state(
            request.app.state.storage,
            request.app.state.store,
            request.app.state.schema_extractor,
            settings.data_base_uri,
        )
    except StorageError:
        raise  # → app-level storage handler (clean 503 + logged cause)
    except Exception as exc:  # Oxigraph unreachable / malformed response
        raise HTTPException(status_code=503, detail="Store unavailable") from exc

    now = time.time()
    staged, registered, linked = state["staged"], state["registered"], state["linked"]
    staged_ids = {key_file_id(o.key, STAGED_PREFIX) for o in staged}
    registered_ids = {key_file_id(o.key, REGISTERED_PREFIX) for o in registered}
    return {
        "configured": True,
        "staged": {
            "count": len(staged),
            "bytes": sum(o.size for o in staged),
            "oldestAgeS": round(now - min(o.last_modified for o in staged)) if staged else None,
        },
        "registered": {
            "count": len(registered),
            "bytes": sum(o.size for o in registered),
        },
        "linkedNodes": len(linked),
        "orphanedNodes": len(state["typed"] - linked),
        "unreferencedStaged": len(staged_ids - linked),
        "unreferencedRegistered": len(registered_ids - linked),
    }
