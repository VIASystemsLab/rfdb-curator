"""Data write routes: create/update and delete RDF entities.

The only routes in the stack that mutate the store. Reads (list, counts, fetch
one) live in ``dataexplorer-backend/api/data.py``. The IRI guard both use is
``rfdb_core.iri``.

Validation merge planning lives in :mod:`core.validation_merge`.
This module keeps the route handlers and the small request-scoped helpers they need.
"""

import json
import logging
import tempfile

from fastapi import APIRouter, HTTPException, Query, Request, status
from rdflib import XSD, BNode, Graph, Literal, URIRef
from rdflib.term import Node

from core.config import settings
from core.node_ids import MintError, mint_iri
from core.validation_merge import (
    _build_validation_construct,
    all_bridge_link_predicates,
    bridge_links,
    shape_field_predicates,
)
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
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


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
# Naming, ownership and dependents
# ---------------------------------------------------------------------------

# Records listed in a refused delete's message; one more is fetched to know
# whether to add "and more".
_MAX_DEPENDENTS_SHOWN = 10


def _select(store, sparql: str) -> list[dict[str, str | None]]:
    """Run a SELECT; a store failure is a 503, as everywhere in the write path."""
    try:
        return store.query(sparql)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Store unavailable") from exc


def _has_triples(store, iri: str) -> bool:
    """Whether the store describes ``iri`` (has it as a subject)."""
    return bool(
        _select(store, f"SELECT ?p {store.from_clause()} WHERE {{ <{iri}> ?p ?o }} LIMIT 1")
    )


def _iri_in_use(store, iri: str) -> bool:
    """Whether the store mentions ``iri`` at all, as subject or object."""
    return bool(
        _select(
            store,
            f"SELECT ?x {store.from_clause()} "
            f"WHERE {{ {{ <{iri}> ?p ?x }} UNION {{ ?x ?p <{iri}> }} }} LIMIT 1",
        )
    )


def _mint(store, shape_id: str, minted: set[str]) -> str:
    """A new IRI for a node of ``shape_id``, unused in the store and in this save."""
    try:
        iri = mint_iri(
            settings.data_base_uri,
            shape_id,
            lambda candidate: candidate in minted or _iri_in_use(store, candidate),
        )
    except MintError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    minted.add(iri)
    return iri


def _rename(graph: Graph, old: Node, new: URIRef) -> None:
    """Replace ``old`` by ``new`` in every triple of ``graph``."""
    for s, p, o in list(graph.triples((old, None, None))):
        graph.remove((s, p, o))
        graph.add((new, p, o))
    for s, p, o in list(graph.triples((None, None, old))):
        graph.remove((s, p, o))
        graph.add((s, p, new))


def _name_bridge_cards(
    data_graph: Graph,
    entity_uri: URIRef,
    store,
    bridges: dict[str, str],
    is_create: bool,
    minted: set[str],
) -> set[URIRef]:
    """Name the new bridge cards; check the existing ones are this record's.

    A new card is a blank node and gets ``<DATA_BASE_URI><BridgeShape>_<12hex>``.
    A card with an IRI must already be linked from this record through the same
    predicate: a bridge node belongs to one record, so anything else (another
    record's card, a Person, reference data) is refused.

    Returns:
        The record's cards as sent, all named.
    """
    cards: set[URIRef] = set()
    for pred, bridge_shape in bridges.items():
        sent = list(data_graph.objects(entity_uri, URIRef(pred)))
        if not sent:
            continue
        stored = set() if is_create else _linked_objects(store, str(entity_uri), pred)
        for card in sent:
            if isinstance(card, BNode):
                named = URIRef(_mint(store, bridge_shape, minted))
                _rename(data_graph, card, named)
                cards.add(named)
            elif isinstance(card, URIRef):
                if str(card) not in stored:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"{card.n3()} is not one of this record's connections through "
                            f"<{pred}>. A new connection is sent without @id."
                        ),
                    )
                if (card, None, None) not in data_graph:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Connection {card.n3()} is sent without its values; send it in full."
                        ),
                    )
                cards.add(card)
    return cards


def _linked_objects(store, entity_id: str, pred: str) -> set[str]:
    """The IRIs ``entity_id`` links through ``pred`` in the store."""
    rows = _select(store, f"SELECT ?o {store.from_clause()} WHERE {{ <{entity_id}> <{pred}> ?o }}")
    return {row["o"] for row in rows if row.get("o")}


def _refuse_other_subjects(data_graph: Graph, entity_uri: URIRef, cards: set[URIRef]) -> None:
    """Refuse a payload that describes anything but the record, its cards and its copies.

    Other records are referenced by a bare ``@id``: describing one would write
    onto it without validating it as itself. A blank node left over here is not
    a card (cards were named above), so it would be stored as an anonymous node.
    """
    allowed = {entity_uri} | cards
    for s, _p, o in data_graph:
        if isinstance(o, BNode):
            raise HTTPException(
                status_code=422,
                detail="Only a new connection may be a blank node; reference records by @id.",
            )
        if s in allowed:
            continue
        # Checked, and named, by _reconcile_digital_copies.
        if (s, _RDF_TYPE, URIRef(SCHEMA_DIGITAL_DOCUMENT)) in data_graph:
            continue
        raise HTTPException(
            status_code=422,
            detail=(
                f"A save describes only its record and the record's connections, not "
                f"{s.n3()}. Reference other records by @id alone."
            ),
        )


def _dependents(store, entity_id: str, links: list[str]) -> list[tuple[str, str | None]]:
    """Records pointing at ``entity_id``, directly or through one of their bridge nodes.

    A link from a bridge node counts as a link from the record owning it.
    Returns up to ``_MAX_DEPENDENTS_SHOWN + 1`` ``(iri, label)`` pairs.
    """
    owner = ""
    if links:
        values = " ".join(f"<{link}>" for link in links)
        owner = f"OPTIONAL {{ VALUES ?link {{ {values} }} ?owner ?link ?s . }}"
    rows = _select(
        store,
        f"""
        SELECT ?record (SAMPLE(?l) AS ?label) {store.from_clause()}
        WHERE {{
            ?s ?p <{entity_id}> .
            {owner}
            BIND(COALESCE(?owner, ?s) AS ?record)
            FILTER(?record != <{entity_id}>)
            OPTIONAL {{ ?record <{_RDFS_LABEL}> ?l }}
        }}
        GROUP BY ?record
        ORDER BY ?record
        LIMIT {_MAX_DEPENDENTS_SHOWN + 1}
        """,
    )
    return [(row["record"], row.get("label")) for row in rows if row.get("record")]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/entities", response_model=DataCreateResponse)
def create_or_update_entity(payload: EntityData, request: Request):
    """Create or update a record from a JSON-LD payload.

    ``@id`` decides which: a payload without it creates a record, named here
    (:mod:`core.node_ids`); a payload with it updates that record, which must
    exist.

    A save describes the record, its bridge nodes and its digital copies, and
    nothing else: other records are referenced by a bare ``@id``.

    On an update, each field of the shape the payload carries replaces the stored
    values; a field it leaves out is replaced only when ``originalTriples`` lists
    it (how the editor says it emptied the field). For a bridge predicate the
    payload is the whole list of cards: cards that exist keep their IRI and must
    already be this record's; new cards come as blank nodes and are named here;
    stored cards the payload drops are deleted. Each card's stored triples are
    replaced by the payload's.

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

    store = request.app.state.store
    data = payload.data
    provided_id = data.get("@id")
    is_create = provided_id is None
    minted: set[str] = set()

    if is_create:
        entity_id = _mint(store, payload.shapeId, minted)
        data = {**data, "@id": entity_id}
    else:
        if not isinstance(provided_id, str):
            raise HTTPException(status_code=422, detail="@id must be an IRI string")
        _validate_iri(provided_id)
        entity_id = provided_id
        if not _has_triples(store, entity_id):
            raise HTTPException(
                status_code=404,
                detail=f"No record {entity_id} to update. A new record is sent without @id.",
            )

    existing_entity_graph = None
    if not is_create:
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

    entity_uri = URIRef(entity_id)
    dep_graph = request.app.state.shape_dep_graph
    bridges = bridge_links(dep_graph, payload.shapeId)
    original_predicates = {t.predicate for t in payload.originalTriples or []}
    # Fields of the shape the payload carries: their values replace the stored ones.
    fields_carried = {
        pred
        for pred in shape_field_predicates(dep_graph, payload.shapeId)
        if (entity_uri, URIRef(pred), None) in data_graph
    }
    bridges_in_play = {
        pred
        for pred in bridges
        if (entity_uri, URIRef(pred), None) in data_graph or pred in original_predicates
    }
    cards = _name_bridge_cards(data_graph, entity_uri, store, bridges, is_create, minted)
    _refuse_other_subjects(data_graph, entity_uri, cards)

    # Digital copies (upload-first flow): the server is the metadata authority.
    # Re-derives staged-node metadata / strips registered-node metadata BEFORE
    # validation, so what is validated is exactly what will be stored. Returns
    # the staged file ids to promote after a successful persist.
    files_to_promote = _reconcile_digital_copies(data_graph, request)

    # Collect seed IRIs for shape-driven merge planning.
    # Include the entity IRI itself only on create flows so root-shape chains
    # can anchor from the entity node (e.g. Manifestation -> R4_embodies ->
    # Expression) even before that node exists in store.
    #
    # On updates, avoid seeding with entity IRI to prevent old store triples of
    # the entity from masking payload intent (e.g. keeping fields the curator
    # deleted in the editor, or causing false maxCount violations via stale
    # values).
    seed_iris = {entity_id} if is_create else set()
    seed_iris |= {
        str(obj)
        for obj in data_graph.objects()
        if isinstance(obj, URIRef) and _is_non_vocab_iri(str(obj))
    }

    # Same reasoning one hop down: the payload's cards replace the stored ones,
    # so merging the store's copy would only re-introduce the value the curator
    # just changed and fail sh:maxCount on the card they edited. Records merely
    # *referenced* by IRI stay seeded: their constraints still need the store's
    # triples.
    seed_iris -= {str(card) for card in cards}
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

    # Validation passed. On an update, delete what is being replaced before
    # writing: every field of the shape the payload carries, the predicates the
    # client lists as changed (an emptied field is not carried), and every bridge
    # predicate in play together with the cards it links. The payload re-adds
    # what is kept.
    if not is_create:
        predicates_to_delete = original_predicates | bridges_in_play | fields_carried
        for pred_uri in predicates_to_delete:
            _validate_iri(pred_uri)

        def cards_where(pred_uri: str) -> str:
            return f"<{entity_id}> <{pred_uri}> ?card . ?card ?cp ?co ."

        # The rollback snapshot above holds only <entity_id> ?p ?o. Add the
        # cards the delete removes, before anything is deleted, so a failed load
        # restores each card together with its link.
        try:
            for pred_uri in bridges_in_play:
                existing_entity_graph += store.construct(
                    f"""
                    CONSTRUCT {{ ?card ?cp ?co }}
                    {store.from_clause()}
                    WHERE {{ {cards_where(pred_uri)} }}
                    """
                )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Store unavailable") from exc

        wc = store.with_clause()
        for pred_uri in predicates_to_delete:
            if pred_uri in bridges_in_play:
                store.update(
                    f"""
                    {wc}
                    DELETE {{ ?card ?cp ?co . }}
                    WHERE  {{ {cards_where(pred_uri)} }}
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
    """Delete a record and its bridge nodes, unless other records point to it.

    The IRI travels as ``?id=``, the one convention for addressing an entity on the
    operational surface — same as ``/graph/node`` and the reader's ``/entities/get``.
    No verb segment is needed here because ``DELETE`` already is the verb, and no
    literal sibling exists under ``/entities`` to collide with; the uniformity is
    about how the *IRI* is passed, which is what actually trips callers up. A
    path-encoded IRI needs double-encoding to survive, and a bare local name fails
    the unsafe-IRI guard with a 400 that reads like a data problem.

    A record that does not exist is a 404. A record another record points to,
    directly or through one of that record's bridge nodes (a Person in a Work's
    AgentRole), is refused with 409 naming them: deleting it would leave them
    unsaveable until the link is fixed.

    An optional ``shapeId`` query parameter may be supplied by the frontend to
    enable per-shape write protection via ``READ_ONLY_SHAPES``.  When absent,
    only the global ``READ_ONLY`` flag is checked.
    """
    _assert_writable_mode()
    if shapeId:
        _assert_shape_writable(shapeId)
    _validate_iri(entity_id)
    store = request.app.state.store
    if not _has_triples(store, entity_id):
        raise HTTPException(status_code=404, detail=f"No record <{entity_id}> to delete.")
    links = sorted(all_bridge_link_predicates(request.app.state.shape_dep_graph))

    dependents = _dependents(store, entity_id, links)
    if dependents:
        shown = dependents[:_MAX_DEPENDENTS_SHOWN]
        names = "; ".join(f"{label} <{iri}>" if label else f"<{iri}>" for iri, label in shown)
        more = " and more" if len(dependents) > _MAX_DEPENDENTS_SHOWN else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete <{entity_id}>: other records point to it. "
                f"Remove those links first: {names}{more}."
            ),
        )

    # Digital copies attached to this entity are NOT purged here: their node
    # triples become orphans (no inbound link) and scripts/cleanup_files.py
    # collects both the triples and the stored objects. One cleanup mechanism,
    # no cross-store transaction in the request path.
    wc = store.with_clause()
    if links:
        # Cards first: if the record delete then fails, nothing is left pointing
        # at a card that is gone.
        values = " ".join(f"<{link}>" for link in links)
        store.update(
            f"""
            {wc}
            DELETE {{ ?card ?cp ?co . }}
            WHERE  {{ VALUES ?link {{ {values} }} <{entity_id}> ?link ?card . ?card ?cp ?co . }}
            """
        )
    store.update(
        f"""
        {wc}
        DELETE {{ <{entity_id}> ?p ?o . }}
        WHERE  {{ <{entity_id}> ?p ?o . }}
        """
    )
    return
