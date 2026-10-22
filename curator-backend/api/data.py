"""Data write routes: create/update and delete RDF entities.

The only routes in the stack that mutate the store. Reads (list, counts, fetch
one) live in ``dataexplorer-backend/api/data.py``; they shared nothing with this
module but the IRI guard, which is now in ``rfdb_core.iri``.

Validation merge planning lives in :mod:`core.validation_merge`.
This module keeps the route handlers and the small request-scoped helpers they need.
"""

import json
import logging
import tempfile

from fastapi import APIRouter, HTTPException, Query, Request, status
from rdflib import XSD, Graph, Literal, URIRef

from core.blank_node_handler import assign_entity_id, skolemize
from core.config import settings
from core.validation_merge import _build_validation_construct, bridge_link_predicates
from models.data import (
    DataCreateResponse,
    EntityData,
    ValidationResult,
)
from rfdb_core.file_storage import registered_key, staged_key
from rfdb_core.files_state import FILE_ID_RE, file_content_url
from rfdb_core.iri import iri_error
from rfdb_core.vocab import (
    SCHEMA_CONTENT_SIZE,
    SCHEMA_CONTENT_URL,
    SCHEMA_DERIVED_TERMS,
    SCHEMA_DIGITAL_DOCUMENT,
    SCHEMA_ENCODING_FORMAT,
    SCHEMA_NAME,
    SCHEMA_NUMBER_OF_PAGES,
    SCHEMA_SHA256,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_VOCAB_PREFIXES = (
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2002/07/owl#",
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/ns/shacl#",
)
_RDF_TYPE = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")


# ---------------------------------------------------------------------------
# IRI validation
# ---------------------------------------------------------------------------


def _validate_iri(entity_id: str) -> None:
    """Reject non-http(s) or unsafe IRIs before interpolating them in SPARQL.

    The rule itself lives in ``rfdb_core.iri`` (both services enforce it); this
    is only the HTTP mapping, which is the service's concern.
    """
    if reason := iri_error(entity_id):
        raise HTTPException(status_code=400, detail=reason)


def _is_non_vocab_iri(value: str) -> bool:
    return not any(value.startswith(prefix) for prefix in _VOCAB_PREFIXES)


def _assert_writable_mode() -> None:
    """Refuse mutating operations when READ_ONLY mode is enabled."""
    if settings.read_only:
        raise HTTPException(
            status_code=403,
            detail="Editor is in read-only mode (READ_ONLY=true). Write operations are disabled.",
        )


def _assert_shape_writable(shape_id: str) -> None:
    """Refuse mutating operations on shapes listed in READ_ONLY_SHAPES."""
    if shape_id in settings.read_only_shapes:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Shape '{shape_id}' is read-only (READ_ONLY_SHAPES). "
                "Write operations are disabled for this shape."
            ),
        )


# ---------------------------------------------------------------------------
# Digital copies (upload-first flow)
# ---------------------------------------------------------------------------


def _reconcile_digital_copies(data_graph: Graph, request: Request) -> list[str]:
    """Make the server the metadata authority for digital-copy nodes in a payload.

    For every ``schema:DigitalDocument`` subject in ``data_graph``:

    - **registered** object exists → strip the round-tripped metadata triples
      (store values are authoritative; also avoids duplicate-literal SHACL
      violations on update);
    - **staged** object exists → re-derive size/sha256/pages/contentUrl from the
      actual bytes and replace the payload's values (client prefill is UI-only);
    - neither → 422 (the staged file expired or was never uploaded).

    Runs BEFORE validation so what is validated is exactly what will be stored.

    Returns:
        The staged file ids to promote to ``registered/`` after a successful
        persist.

    Raises:
        HTTPException 422: malformed node IRI or missing staged object.
        HTTPException 503: storage not configured.
    """
    nodes = list(data_graph.subjects(_RDF_TYPE, URIRef(SCHEMA_DIGITAL_DOCUMENT)))
    if not nodes:
        return []

    # Lazy import: api.files imports this module at load time, so importing it
    # back at module level would be a circular import.
    from api.files import derive_metadata

    storage = request.app.state.storage
    to_promote: list[str] = []
    for node in nodes:
        iri = str(node)
        base = settings.data_base_uri
        file_id = iri[len(base) :] if iri.startswith(base) else ""
        if not FILE_ID_RE.match(file_id):
            raise HTTPException(status_code=422, detail=f"Invalid digital-copy node IRI: {iri}")

        # A StorageError from exists()/open_stream() propagates to the app-level
        # handler (clean 503). Only "file genuinely absent" is a 422 here.
        if storage.exists(registered_key(file_id)):
            for term in (*SCHEMA_DERIVED_TERMS, SCHEMA_NAME):
                data_graph.remove((node, URIRef(term), None))
            continue
        if not storage.exists(staged_key(file_id)):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Staged file '{file_id}' not found — it may have been "
                    "cleaned up. Re-upload the file and retry."
                ),
            )

        # Staged: spool the object back and re-derive the metadata server-side.
        with tempfile.TemporaryFile() as spool:
            for chunk in storage.open_stream(staged_key(file_id)):
                spool.write(chunk)
            size, sha256, pages = derive_metadata(spool)

        for term in SCHEMA_DERIVED_TERMS:
            data_graph.remove((node, URIRef(term), None))
        data_graph.add((node, URIRef(SCHEMA_ENCODING_FORMAT), Literal("application/pdf")))
        data_graph.add(
            (
                node,
                URIRef(SCHEMA_CONTENT_URL),
                Literal(file_content_url(file_id, settings.data_base_uri), datatype=XSD.anyURI),
            )
        )
        data_graph.add((node, URIRef(SCHEMA_CONTENT_SIZE), Literal(size)))  # int → xsd:integer
        data_graph.add((node, URIRef(SCHEMA_SHA256), Literal(sha256)))
        if pages is not None:
            data_graph.add((node, URIRef(SCHEMA_NUMBER_OF_PAGES), Literal(pages)))
        to_promote.append(file_id)
    return to_promote


def _promote_staged_files(request: Request, file_ids: list[str]) -> None:
    """Move staged objects to ``registered/`` after a successful persist.

    A failed move is logged, not raised: the triples are already persisted, so
    the file is referenced-but-staged — exactly the state the cleanup script
    promotes on its next run (crash-safe by design).
    """
    for file_id in file_ids:
        try:
            request.app.state.storage.move(staged_key(file_id), registered_key(file_id))
        except Exception:
            logger.warning(
                "Failed to promote staged file '%s' — cleanup_files.py will retry",
                file_id,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/entities", response_model=DataCreateResponse)
def create_or_update_entity(payload: EntityData, request: Request):
    """Create or update an entity (RDF resource) from JSON-LD payload.

    SHACL targeting nuance:
    - Constraints attached to class-targeted shapes (sh:targetClass) are
        evaluated only for nodes that declare that class.
    - If a node is missing its expected RDF type, those class-targeted
        constraints may not be evaluated for that node.
    - Callers should therefore emit explicit @type values for entities and
        bridge/helper nodes whenever schema constraints depend on class targeting.

    Validation scope:
    - The full merged validation graph is validated (incoming payload +
        referenced entities merged from store), so nested linked-node
        constraint violations are rejected within the same write request.
    """
    _assert_writable_mode()
    _assert_shape_writable(payload.shapeId)

    data = payload.data
    provided_id = data.get("@id")
    if isinstance(provided_id, str):
        _validate_iri(provided_id)

    data = assign_entity_id(data, payload.shapeId, settings.data_base_uri)
    entity_id = data.get("@id", "")
    data = skolemize(data, entity_id, settings.data_base_uri)

    store = request.app.state.store
    existing_entity_graph = None

    if entity_id and payload.originalTriples:
        _validate_iri(entity_id)
        try:
            existing_entity_graph = store.construct(
                f"""
                CONSTRUCT {{ <{entity_id}> ?p ?o }}
                {store.from_clause()}
                WHERE {{ <{entity_id}> ?p ?o }}
                """
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Store unavailable") from exc

    # Parse JSON-LD payload into an rdflib Graph.
    data_graph = Graph()
    try:
        data_graph.parse(data=json.dumps(data), format="json-ld")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON-LD: {exc}") from exc

    # Digital copies (upload-first flow): the server is the metadata authority.
    # Re-derives staged-node metadata / strips registered-node metadata BEFORE
    # validation, so what is validated is exactly what will be stored. Returns
    # the staged file ids to promote after a successful persist.
    files_to_promote = _reconcile_digital_copies(data_graph, request)

    entity_uri = URIRef(entity_id) if entity_id.startswith("http") else None

    # Collect seed IRIs for shape-driven merge planning.
    # Include the entity IRI itself only on create flows so root-shape chains
    # can anchor from the entity node (e.g. Manifestation -> R4_embodies ->
    # Expression) even before that node exists in store.
    #
    # On updates, avoid seeding with entity IRI to prevent old store triples of
    # the entity from masking payload intent (e.g. preserving removed fields or
    # causing false maxCount violations via stale values).
    include_root_entity = bool(entity_uri is not None and not payload.originalTriples)
    seed_iris = {str(entity_uri)} if include_root_entity else set()
    seed_iris |= {
        str(obj)
        for obj in data_graph.objects()
        if isinstance(obj, URIRef) and _is_non_vocab_iri(str(obj))
    }

    # Same reasoning as include_root_entity, one hop down: an inline bridge node
    # (e.g. AgentRole) that the payload describes is payload-authoritative — the
    # editor always sends the whole node and the write below replaces its stored
    # triples — so merging the store's copy would only re-introduce the value the
    # curator just changed and fail sh:maxCount on the node they edited. Nodes
    # merely *referenced* by IRI stay seeded: their constraints still need the
    # store's triples.
    bridge_predicates = bridge_link_predicates(request.app.state.shape_dep_graph, payload.shapeId)
    seed_iris -= {
        str(obj)
        for pred in bridge_predicates
        for obj in data_graph.objects(predicate=URIRef(pred))
        if isinstance(obj, URIRef) and (obj, None, None) in data_graph
    }
    logger.debug("Validation merge seed IRIs (%d): %s", len(seed_iris), sorted(seed_iris))

    # Build the validation graph by merging the payload with the referenced
    # entities fetched from Oxigraph.
    #
    # The fetch plan is derived from the shape dependency graph.  See
    # core.validation_merge for the query-planning logic.
    #
    # Fail closed: if the referenced graph cannot be resolved, do not proceed
    # with an incomplete validation graph.  A partial merge is worse than a
    # clean 503 because it may silently pass constraints that should fail.
    referenced_graph = Graph()
    if seed_iris:
        dep_graph = request.app.state.shape_dep_graph
        construct_query = _build_validation_construct(
            dep_graph=dep_graph,
            root_shape_id=payload.shapeId,
            seed_iris=seed_iris,
            from_clause=store.from_clause(),
        )
        try:
            referenced_graph = store.construct(construct_query) if construct_query else Graph()
            validation_graph = referenced_graph + data_graph
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Related-entity merge failed: {exc}",
            ) from exc
    else:
        validation_graph = data_graph
    logger.debug(
        "Validation merge graph triple counts data=%d referenced=%d merged=%d",
        len(data_graph),
        len(referenced_graph),
        len(validation_graph),
    )

    # Validate the merged graph against the SHACL schema.
    # Full-graph validation is intentional so nested linked-node violations
    # (e.g. missing required fields inside referenced structures) are surfaced
    # and rejected in the same write request.
    validator = request.app.state.shacl_validator
    report = validator.validate(validation_graph)

    if not report["conforms"]:
        logger.warning(
            "SHACL validation failed for entity %s (shape=%s, violations=%d)",
            entity_id,
            payload.shapeId,
            len(report.get("violations", [])),
        )
        return DataCreateResponse(
            success=False,
            entityId=entity_id,
            validationReport=ValidationResult(**report),
        )

    # Validation passed.  Delete the predicates that are being updated before
    # writing the new triples.
    if entity_id and payload.originalTriples:
        predicates_to_delete = {t.predicate for t in payload.originalTriples}
        for pred_uri in predicates_to_delete:
            _validate_iri(pred_uri)

        def bridge_where(pred_uri: str) -> str:
            # The bridge nodes reached from this entity through pred_uri and by
            # no other link: the ones the delete below removes.
            return f"""
                <{entity_id}> <{pred_uri}> ?bridge .
                ?bridge ?bp ?bo .
                FILTER NOT EXISTS {{
                    ?other ?op ?bridge .
                    FILTER(?other != <{entity_id}> || ?op != <{pred_uri}>)
                }}
            """

        # The rollback snapshot above holds only <entity_id> ?p ?o. Add the
        # bridge triples the delete removes, before anything is deleted, so a
        # failed load restores each node together with its link.
        try:
            for pred_uri in predicates_to_delete & bridge_predicates:
                existing_entity_graph += store.construct(
                    f"""
                    CONSTRUCT {{ ?bridge ?bp ?bo }}
                    {store.from_clause()}
                    WHERE {{ {bridge_where(pred_uri)} }}
                    """
                )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Store unavailable") from exc

        wc = store.with_clause()
        for pred_uri in predicates_to_delete:
            if pred_uri in bridge_predicates:
                # A helper-bridge node (e.g. AgentRole) has no existence apart
                # from the link being rewritten: deleting only the link would
                # leave its triples in the store as an orphan the editor can no
                # longer reach but validation merges can still pull in. Delete
                # the node too — unless any link other than the one being
                # rewritten points at it, in which case it is shared and stays.
                # Payload-asserted bridge nodes are re-written by the load below,
                # so this replaces (rather than accumulates) their values on edit.
                store.update(
                    f"""
                    {wc}
                    DELETE {{ ?bridge ?bp ?bo . }}
                    WHERE  {{ {bridge_where(pred_uri)} }}
                    """
                )
            store.update(
                f"""
                {wc}
                DELETE {{ <{entity_id}> <{pred_uri}> ?o . }}
                WHERE  {{ <{entity_id}> <{pred_uri}> ?o . }}
                """
            )

    # Serialize to Turtle and bulk-load into Oxigraph.
    # On write failure, attempt to restore the pre-existing entity graph if
    # one was captured above.
    turtle_data = data_graph.serialize(format="turtle")

    try:
        store.load_turtle(turtle_data)
    except Exception as exc:
        if existing_entity_graph is not None and len(existing_entity_graph) > 0:
            try:
                store.load_turtle(existing_entity_graph.serialize(format="turtle"))
            except Exception as restore_exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Store write failed and rollback failed: {restore_exc}",
                ) from restore_exc
        raise HTTPException(status_code=503, detail=f"Store write failed: {exc}") from exc

    # Triples are persisted — promote any newly staged digital copies.
    _promote_staged_files(request, files_to_promote)

    return DataCreateResponse(
        success=True,
        entityId=entity_id,
        validationReport=ValidationResult(**report),
    )


@router.delete("/entities", status_code=status.HTTP_204_NO_CONTENT)
def delete_entity(
    request: Request,
    entity_id: str = Query(..., alias="id", description="Full IRI of the entity to delete"),
    shapeId: str = Query(
        "", description="Shape URI of the entity being deleted (used to enforce READ_ONLY_SHAPES)"
    ),
):
    """Delete all triples for a given entity IRI.

    The IRI travels as ``?id=``, the one convention for addressing an entity on the
    operational surface — same as ``/graph/node`` and the reader's ``/entities/get``.
    No verb segment is needed here because ``DELETE`` already is the verb, and no
    literal sibling exists under ``/entities`` to collide with; the uniformity is
    about how the *IRI* is passed, which is what actually trips callers up. A
    path-encoded IRI needs double-encoding to survive, and a bare local name fails
    the unsafe-IRI guard with a 400 that reads like a data problem.

    An optional ``shapeId`` query parameter may be supplied by the frontend to
    enable per-shape write protection via ``READ_ONLY_SHAPES``.  When absent,
    only the global ``READ_ONLY`` flag is checked.
    """
    _assert_writable_mode()
    if shapeId:
        _assert_shape_writable(shapeId)
    _validate_iri(entity_id)
    store = request.app.state.store

    # Digital copies attached to this entity are NOT purged here: their node
    # triples become orphans (no inbound link) and scripts/cleanup_files.py
    # collects both the triples and the stored objects. One cleanup mechanism,
    # no cross-store transaction in the request path.

    # TODO: Delete also triples where this entity is the object and related
    #   blank/helper nodes (e.g. skolemized AgentRole nodes exclusively owned
    #   by this entity).  Orphaned helper nodes left behind here will cause
    #   SHACL validation failures on unrelated entities that trigger a merge
    #   including those IRIs.  Deletion of helper nodes must happen before the
    #   entity delete to avoid leaving orphans if a partial failure occurs.
    #   Guard with FILTER NOT EXISTS to avoid deleting shared helper nodes —
    #   the update path above already does exactly that for the predicates in
    #   bridge_link_predicates(); reuse that query shape here.
    delete_query = f"""
        {store.with_clause()}
        DELETE {{ <{entity_id}> ?p ?o . }}
        WHERE  {{ <{entity_id}> ?p ?o . }}
    """

    store.update(delete_query)
    return
