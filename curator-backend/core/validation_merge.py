"""Validation merge planner for incremental SHACL writes.

This module is intentionally pure: it parses the extracted SHACL shape metadata
into a small dependency graph and compiles that graph into a SPARQL CONSTRUCT
query. It has no FastAPI, Oxigraph, or request-handling responsibilities.

The data-entry route uses the exported helpers as follows:

1. Build the shape dependency graph once at startup.
2. On each write request, derive the minimal validation CONSTRUCT for the
   requested root shape and the payload seed IRIs.
3. Execute that query against Oxigraph and validate the merged graph.

The compiler is shape-driven and bounded by shape connectivity, not by a data
Graph closure heuristic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class _ShapeEdge:
    """One predicate-level hop from a shape to a related shape."""

    predicate_uri: str
    target_shape_id: str
    # True when the hop lands on a helper-bridge shape edited inline (field type
    # ``nested``, e.g. AgentRole): the node exists only to connect its parent to
    # other entities and the payload always describes it in full, so the parent
    # owns it and the write path may delete it along with the link.
    # Deliberately False for ``file-list`` (digital copies), whose nested shape
    # is label-less too but whose node metadata is owned by the *store*
    # (``_reconcile_digital_copies`` strips it from the payload) — deleting those
    # nodes on a rewrite would destroy the file metadata.
    inline_bridge: bool = False


@dataclass
class _ShapeNode:
    """A single SHACL NodeShape with its outbound constraint edges."""

    shape_id: str
    target_class_uri: str | None
    edges: list[_ShapeEdge] = field(default_factory=list)
    # Every predicate the shape declares a property for: the record's fields.
    field_predicates: set[str] = field(default_factory=set)


def build_shape_dep_graph(extractor) -> dict[str, _ShapeNode]:
    """Build the shape dependency graph keyed by shape IRI."""
    nodes: dict[str, _ShapeNode] = {}
    for shape in extractor.get_all_shapes():
        sid = shape.get("id")
        if not sid:
            continue

        node = _ShapeNode(
            shape_id=sid,
            target_class_uri=shape.get("targetClassUri"),
        )

        for prop in shape.get("properties", []):
            predicate = prop.get("pathUri")
            if predicate:
                node.field_predicates.add(predicate)
            nested = prop.get("nestedShape")
            if predicate and nested:
                node.edges.append(
                    _ShapeEdge(
                        predicate,
                        nested,
                        prop.get("nestedShapeRole") == "helper-bridge"
                        and prop.get("type") == "nested",
                    )
                )

        nodes[sid] = node

    return nodes


def bridge_links(dep_graph: dict[str, _ShapeNode], shape_id: str) -> dict[str, str]:
    """Predicates of ``shape_id`` whose objects are inline bridge nodes, with their shape.

    A bridge node belongs to the one record linking it through one of these
    predicates: the write path names new ones after the bridge shape, refuses
    one owned by another record, and deletes the ones a save drops. A standalone
    entity (or a store-owned digital copy) is never in this map. See
    :class:`_ShapeEdge.inline_bridge` for the exact rule.
    """
    node = dep_graph.get(shape_id)
    if not node:
        return {}
    return {edge.predicate_uri: edge.target_shape_id for edge in node.edges if edge.inline_bridge}


def shape_field_predicates(dep_graph: dict[str, _ShapeNode], shape_id: str) -> set[str]:
    """The predicates ``shape_id`` declares properties for; empty for an unknown shape."""
    node = dep_graph.get(shape_id)
    return set(node.field_predicates) if node else set()


def all_bridge_link_predicates(dep_graph: dict[str, _ShapeNode]) -> set[str]:
    """Every predicate, of any shape, that links a record to its bridge nodes."""
    return {
        edge.predicate_uri
        for node in dep_graph.values()
        for edge in node.edges
        if edge.inline_bridge
    }


def _bfs_shape_edges(
    dep_graph: dict[str, _ShapeNode],
    root_shape_id: str,
) -> list[tuple[_ShapeNode, tuple[_ShapeEdge, ...]]]:
    """Return shape nodes and their edge paths in BFS discovery order."""
    root = dep_graph.get(root_shape_id)
    if not root:
        return []

    results: list[tuple[_ShapeNode, tuple[_ShapeEdge, ...]]] = []
    queue: deque = deque([(root, tuple(), frozenset({root_shape_id}))])

    while queue:
        node, path, visited = queue.popleft()
        for edge in node.edges:
            next_node = dep_graph.get(edge.target_shape_id)
            if not next_node:
                continue
            next_path = (*path, edge)
            results.append((next_node, next_path))
            if edge.target_shape_id not in visited:
                queue.append((next_node, next_path, visited | {edge.target_shape_id}))

    return results


def build_validation_construct(
    dep_graph: dict[str, _ShapeNode],
    root_shape_id: str,
    seed_iris: set[str],
    from_clause: str,
) -> str:
    """Compile a SPARQL CONSTRUCT that fetches the triples needed for validation."""
    if not seed_iris:
        return ""

    paths = _bfs_shape_edges(dep_graph, root_shape_id)
    values = " ".join(f"<{iri}>" for iri in sorted(seed_iris))

    construct_triples: list[str] = []
    where_blocks: list[str] = []

    construct_triples.append("?seed ?p0 ?o0 .")
    where_blocks.append(f"{{\n    VALUES ?seed {{ {values} }}\n    ?seed ?p0 ?o0 .\n}}")

    # Add one BGP block per path suffix, not only full root-to-terminal paths.
    # This allows anchoring from referenced intermediate nodes that are already
    # in store when leading edges exist only in the incoming payload graph.
    seen_suffixes: set[tuple[str, ...]] = set()
    bgp_idx = 0

    for _node, edge_path in paths:
        predicates = tuple(edge.predicate_uri for edge in edge_path)
        if not predicates:
            continue

        for start in range(len(predicates)):
            suffix = predicates[start:]
            if suffix in seen_suffixes:
                continue
            seen_suffixes.add(suffix)

            terminal_var = f"?t{bgp_idx}"
            pred_var = f"?pT{bgp_idx}"
            obj_var = f"?oT{bgp_idx}"

            bgp_lines: list[str] = [f"    VALUES ?root{bgp_idx} {{ {values} }}"]
            current = f"?root{bgp_idx}"

            for hop, predicate_uri in enumerate(suffix[:-1]):
                next_var = f"?v{bgp_idx}_{hop}"
                bgp_lines.append(f"    {current} <{predicate_uri}> {next_var} .")
                current = next_var

            # Emit terminal_var directly for the last hop. Substituting it into
            # a built line with str.replace() would be unsafe when the variable
            # name appears as a substring of a predicate URI.
            bgp_lines.append(f"    {current} <{suffix[-1]}> {terminal_var} .")
            bgp_lines.append(f"    {terminal_var} {pred_var} {obj_var} .")

            construct_triples.append(f"{terminal_var} {pred_var} {obj_var} .")
            where_blocks.append("{\n" + "\n".join(bgp_lines) + "\n}")
            bgp_idx += 1

    construct_body = "\n    ".join(construct_triples)
    where_body = "\n    UNION\n    ".join(where_blocks)

    return f"CONSTRUCT {{\n    {construct_body}\n}}\n{from_clause}\nWHERE {{\n    {where_body}\n}}"
