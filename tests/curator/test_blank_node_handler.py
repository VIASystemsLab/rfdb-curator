"""Skolem IRIs for nested blank nodes: named under the data base, whatever the prefix."""

from __future__ import annotations

import pytest

from core.blank_node_handler import skolemize

BASE = "https://example.org/rdf/data/"


@pytest.mark.parametrize(
    "parent_id",
    [f"{BASE}L111", "rfdb:L111", "kb:L111", "https://other.org/ns#L111"],
)
def test_nested_blank_node_takes_the_parents_local_name(parent_id: str) -> None:
    """Full IRI or CURIE under any prefix, the nested node is ``<base>L111_<prop>_<i>``."""
    data = {"core:hasAgentRole": [{"@id": "_:temp"}]}
    result = skolemize(data, parent_id, BASE)
    assert result["core:hasAgentRole"][0]["@id"] == f"{BASE}L111_hasAgentRole_0"
