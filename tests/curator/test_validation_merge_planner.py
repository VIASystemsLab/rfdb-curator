"""Unit coverage for validation-merge planning logic.

This module tests planner behavior in isolation by calling backend planner
helpers directly from core.validation_merge. It validates graph extraction,
BFS traversal semantics, CONSTRUCT query compilation, and parser-level query
execution against a live store.
"""

from __future__ import annotations

import socket
import time
from types import SimpleNamespace

import pytest
from rdflib import Graph


def _load_backend_symbols() -> SimpleNamespace:
    """Load planner and Oxigraph symbols from the backend package."""

    from core.validation_merge import (
        _bfs_shape_edges,
        _build_shape_dep_graph,
        _build_validation_construct,
        _ShapeEdge,
        _ShapeNode,
        all_bridge_link_predicates,
        bridge_links,
    )
    from rfdb_core.triplestore import OxigraphStore

    return SimpleNamespace(
        OxigraphStore=OxigraphStore,
        ShapeEdge=_ShapeEdge,
        ShapeNode=_ShapeNode,
        bfs_shape_edges=_bfs_shape_edges,
        all_bridge_link_predicates=all_bridge_link_predicates,
        bridge_links=bridge_links,
        build_shape_dep_graph=_build_shape_dep_graph,
        build_validation_construct=_build_validation_construct,
    )


class _DummyExtractor:
    """Minimal schema-extractor stub exposing get_all_shapes()."""

    def __init__(self, shapes: list[dict]):
        self._shapes = shapes

    def get_all_shapes(self) -> list[dict]:
        return self._shapes


def _paths_signature(
    paths: list[tuple[object, tuple[object, ...]]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Normalize planner path output to deterministic assertion-friendly tuples."""
    return [
        (node.shape_id, tuple(edge.predicate_uri for edge in edge_path))
        for node, edge_path in paths
    ]


def _oxigraph_reachable(host: str = "127.0.0.1", port: int = 7878) -> bool:
    """Return True when a local Oxigraph endpoint is reachable for smoke tests."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def test_build_shape_dep_graph_filters_invalid_properties() -> None:
    """Only valid nested-shape properties become dependency edges."""
    symbols = _load_backend_symbols()

    extractor = _DummyExtractor(
        [
            {
                "id": "urn:shape:root",
                "targetClassUri": "urn:class:root",
                "properties": [
                    {
                        "pathUri": "urn:pred:valid",
                        "nestedShape": "urn:shape:child",
                    },
                    {"pathUri": "urn:pred:missing_nested"},
                    {"nestedShape": "urn:shape:missing_pred"},
                ],
            },
            {
                "id": "urn:shape:child",
                "targetClassUri": "urn:class:child",
                "properties": [],
            },
            {"targetClassUri": "urn:class:ignored-no-id"},
        ]
    )

    dep_graph = symbols.build_shape_dep_graph(extractor)

    assert set(dep_graph.keys()) == {"urn:shape:root", "urn:shape:child"}
    root_edges = dep_graph["urn:shape:root"].edges
    assert len(root_edges) == 1
    assert root_edges[0].predicate_uri == "urn:pred:valid"
    assert root_edges[0].target_shape_id == "urn:shape:child"


def test_bfs_shape_edges_is_deterministic_and_cycle_safe() -> None:
    """BFS path discovery is stable and terminates even with cycles."""
    symbols = _load_backend_symbols()

    dep_graph = {
        "root": symbols.ShapeNode(
            shape_id="root",
            target_class_uri="urn:class:root",
            edges=[
                symbols.ShapeEdge("urn:pred:r_to_a", "a"),
                symbols.ShapeEdge("urn:pred:r_to_b", "b"),
            ],
        ),
        "a": symbols.ShapeNode(
            shape_id="a",
            target_class_uri="urn:class:a",
            edges=[symbols.ShapeEdge("urn:pred:a_to_c", "c")],
        ),
        "b": symbols.ShapeNode(
            shape_id="b",
            target_class_uri="urn:class:b",
            edges=[symbols.ShapeEdge("urn:pred:b_to_c", "c")],
        ),
        "c": symbols.ShapeNode(
            shape_id="c",
            target_class_uri="urn:class:c",
            edges=[symbols.ShapeEdge("urn:pred:c_to_root", "root")],
        ),
    }

    first = _paths_signature(symbols.bfs_shape_edges(dep_graph, "root"))
    second = _paths_signature(symbols.bfs_shape_edges(dep_graph, "root"))

    assert first == second
    assert ("a", ("urn:pred:r_to_a",)) in first
    assert ("b", ("urn:pred:r_to_b",)) in first
    assert ("c", ("urn:pred:r_to_a", "urn:pred:a_to_c")) in first
    assert ("c", ("urn:pred:r_to_b", "urn:pred:b_to_c")) in first
    assert (
        "root",
        ("urn:pred:r_to_a", "urn:pred:a_to_c", "urn:pred:c_to_root"),
    ) in first
    assert (
        "root",
        ("urn:pred:r_to_b", "urn:pred:b_to_c", "urn:pred:c_to_root"),
    ) in first
    assert len(first) == 6


def test_build_validation_construct_emits_seed_and_suffix_blocks() -> None:
    """CONSTRUCT includes seed triples and deduplicated suffix-path blocks."""
    symbols = _load_backend_symbols()

    dep_graph = {
        "root": symbols.ShapeNode(
            shape_id="root",
            target_class_uri="urn:class:root",
            edges=[symbols.ShapeEdge("urn:pred:r_to_expr", "expr")],
        ),
        "expr": symbols.ShapeNode(
            shape_id="expr",
            target_class_uri="urn:class:expr",
            edges=[symbols.ShapeEdge("urn:pred:expr_to_work", "work")],
        ),
        "work": symbols.ShapeNode(
            shape_id="work",
            target_class_uri="urn:class:work",
            edges=[],
        ),
    }

    empty_query = symbols.build_validation_construct(dep_graph, "root", set(), "")
    assert empty_query == ""

    query = symbols.build_validation_construct(
        dep_graph=dep_graph,
        root_shape_id="root",
        seed_iris={"https://rosfeatr.eu/rdf/data/B", "https://rosfeatr.eu/rdf/data/A"},
        from_clause="FROM <https://rfdb.it/graph/data>",
    )

    assert query.startswith("CONSTRUCT {")
    # Seeds are emitted in sorted order, so the VALUES clause is deterministic.
    expected_values = (
        "VALUES ?seed { <https://rosfeatr.eu/rdf/data/A> <https://rosfeatr.eu/rdf/data/B> }"
    )
    assert expected_values in query
    assert "?seed ?p0 ?o0 ." in query
    assert "FROM <https://rfdb.it/graph/data>" in query
    assert "<urn:pred:r_to_expr>" in query
    assert "<urn:pred:expr_to_work>" in query
    assert "?root0" in query and "?root1" in query and "?root2" in query
    assert "?root3" not in query
    assert query.count("UNION") == 3


def test_construct_query_syntax_smoke_against_oxigraph() -> None:
    """Generated CONSTRUCT query parses and executes against a live store."""
    symbols = _load_backend_symbols()

    if not _oxigraph_reachable():
        pytest.skip("Oxigraph is not reachable on localhost:7878")

    dep_graph = {
        "root": symbols.ShapeNode(
            shape_id="root",
            target_class_uri="urn:class:root",
            edges=[symbols.ShapeEdge("urn:pred:link", "child")],
        ),
        "child": symbols.ShapeNode(
            shape_id="child",
            target_class_uri="urn:class:child",
            edges=[],
        ),
    }

    query = symbols.build_validation_construct(
        dep_graph=dep_graph,
        root_shape_id="root",
        seed_iris={"https://rosfeatr.eu/rdf/data/syntax_seed"},
        from_clause="",
    )

    client = symbols.OxigraphStore("http://127.0.0.1:7878")
    result_graph = client.construct(query)

    assert isinstance(result_graph, Graph)


def test_missing_root_shape_emits_minimal_seed_query() -> None:
    """Unknown root shape degrades safely to a minimal seed-only query."""
    symbols = _load_backend_symbols()

    query = symbols.build_validation_construct(
        dep_graph={},
        root_shape_id="urn:shape:missing",
        seed_iris={"https://rosfeatr.eu/rdf/data/seed"},
        from_clause="",
    )

    assert query.startswith("CONSTRUCT {")
    assert "VALUES ?seed { <https://rosfeatr.eu/rdf/data/seed> }" in query
    assert "?seed ?p0 ?o0 ." in query
    assert "UNION" not in query


def test_root_without_outgoing_edges_emits_minimal_seed_query() -> None:
    """Root shapes with no edges still compile to a valid minimal query."""
    symbols = _load_backend_symbols()

    dep_graph = {
        "urn:shape:root": symbols.ShapeNode(
            shape_id="urn:shape:root",
            target_class_uri="urn:class:root",
            edges=[],
        )
    }

    query = symbols.build_validation_construct(
        dep_graph=dep_graph,
        root_shape_id="urn:shape:root",
        seed_iris={"https://rosfeatr.eu/rdf/data/seed"},
        from_clause="",
    )

    assert query.startswith("CONSTRUCT {")
    assert "VALUES ?seed { <https://rosfeatr.eu/rdf/data/seed> }" in query
    assert "?seed ?p0 ?o0 ." in query
    assert "UNION" not in query


def test_deep_chain_topology_compiles_without_crash() -> None:
    """Deep dependency-only chains compile successfully with expected suffix blocks."""
    symbols = _load_backend_symbols()

    depth = 12
    dep_graph: dict[str, object] = {}
    for idx in range(depth + 1):
        edges = (
            [symbols.ShapeEdge(f"urn:pred:{idx}", f"urn:shape:{idx + 1}")] if idx < depth else []
        )
        dep_graph[f"urn:shape:{idx}"] = symbols.ShapeNode(
            shape_id=f"urn:shape:{idx}",
            target_class_uri=f"urn:class:{idx}",
            edges=edges,
        )

    query = symbols.build_validation_construct(
        dep_graph=dep_graph,
        root_shape_id="urn:shape:0",
        seed_iris={"https://rosfeatr.eu/rdf/data/seed"},
        from_clause="",
    )

    assert "<urn:pred:0>" in query
    assert f"<urn:pred:{depth - 1}>" in query
    # For a depth-N chain, planner emits all unique suffixes from every
    # discovered path, resulting in N*(N+1)/2 suffix blocks plus seed block.
    assert query.count("UNION") == (depth * (depth + 1)) // 2


@pytest.mark.parametrize("topology", ["long_chain", "wide_fanout"])
def test_planner_scale_topologies_have_stable_runtime(topology: str) -> None:
    """Planner remains stable on long-chain and wide-fanout synthetic graphs."""
    symbols = _load_backend_symbols()

    root_shape_id = "urn:shape:root"
    if topology == "long_chain":
        size = 120
        dep_graph = {
            f"urn:shape:{idx}": symbols.ShapeNode(
                shape_id=f"urn:shape:{idx}",
                target_class_uri=f"urn:class:{idx}",
                edges=(
                    [symbols.ShapeEdge(f"urn:pred:{idx}", f"urn:shape:{idx + 1}")]
                    if idx < size
                    else []
                ),
            )
            for idx in range(size + 1)
        }
        root_shape_id = "urn:shape:0"
    else:
        fanout = 250
        dep_graph = {
            "urn:shape:root": symbols.ShapeNode(
                shape_id="urn:shape:root",
                target_class_uri="urn:class:root",
                edges=[
                    symbols.ShapeEdge(f"urn:pred:{idx}", f"urn:shape:{idx}")
                    for idx in range(fanout)
                ],
            )
        }
        dep_graph.update(
            {
                f"urn:shape:{idx}": symbols.ShapeNode(
                    shape_id=f"urn:shape:{idx}",
                    target_class_uri=f"urn:class:{idx}",
                    edges=[],
                )
                for idx in range(fanout)
            }
        )

    started = time.perf_counter()
    query = symbols.build_validation_construct(
        dep_graph=dep_graph,
        root_shape_id=root_shape_id,
        seed_iris={"https://rosfeatr.eu/rdf/data/seed"},
        from_clause="",
    )
    elapsed = time.perf_counter() - started

    assert query.startswith("CONSTRUCT {")
    assert len(query) > 1000
    # Keep this intentionally generous to avoid flaky CI due to host variance.
    assert elapsed < 2.0


def test_bridge_links_only_cover_inline_nested_links() -> None:
    """Only inline-edited bridge links are deletable-with-the-link.

    The write path deletes a bridge node when its link is rewritten, so this set
    decides what may be destroyed. ``file-list`` nested shapes (digital copies)
    are label-less too, hence also "helper-bridge", but their node metadata lives
    in the store — including them here would wipe a kept file's metadata on the
    next save of its parent.
    """
    symbols = _load_backend_symbols()

    extractor = _DummyExtractor(
        [
            {
                "id": "urn:shape:root",
                "targetClassUri": "urn:class:root",
                "properties": [
                    {
                        "pathUri": "urn:pred:agentrole",
                        "nestedShape": "urn:shape:bridge",
                        "nestedShapeRole": "helper-bridge",
                        "type": "nested",
                    },
                    {
                        "pathUri": "urn:pred:digitalcopy",
                        "nestedShape": "urn:shape:bridge",
                        "nestedShapeRole": "helper-bridge",
                        "type": "file-list",
                    },
                    {
                        "pathUri": "urn:pred:place",
                        "nestedShape": "urn:shape:standalone",
                        "nestedShapeRole": "standalone-entity",
                        "type": "entity-search",
                    },
                ],
            },
        ]
    )
    dep_graph = symbols.build_shape_dep_graph(extractor)

    assert symbols.bridge_links(dep_graph, "urn:shape:root") == {
        "urn:pred:agentrole": "urn:shape:bridge"
    }
    assert symbols.all_bridge_link_predicates(dep_graph) == {"urn:pred:agentrole"}
    # Unknown shape: nothing is deletable (fail safe, not fail destructive).
    assert symbols.bridge_links(dep_graph, "urn:shape:absent") == {}
