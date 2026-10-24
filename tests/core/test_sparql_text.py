"""``regex_literal``: typed search text matches itself inside a SPARQL regex.

Each case runs a real SPARQL query through rdflib, so a pattern that is invalid
as a regex or as a string literal fails here the way it would in the store.
"""

from __future__ import annotations

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS

from rfdb_core.sparql_text import regex_literal

LABELS = [
    "Foo (bar)",
    "a.b",
    "axb",
    'say "hi"',
    "back\\slash",
    "C++ [draft] $5 ^up {x|y}",
]


@pytest.fixture(scope="module")
def graph() -> Graph:
    g = Graph()
    for i, label in enumerate(LABELS):
        g.add((URIRef(f"https://example.org/{i}"), RDFS.label, Literal(label)))
    return g


def _matches(graph: Graph, text: str) -> set[str]:
    query = f"""
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?label WHERE {{
            ?s rdfs:label ?label .
            FILTER(regex(str(?label), "{regex_literal(text)}", "i"))
        }}
    """
    return {str(row.label) for row in graph.query(query)}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("(bar", {"Foo (bar)"}),
        ("a.b", {"a.b"}),
        ('"hi"', {'say "hi"'}),
        ("k\\s", {"back\\slash"}),
        ("C++ [draft] $5 ^up {x|y}", {"C++ [draft] $5 ^up {x|y}"}),
        ("FOO", {"Foo (bar)"}),
    ],
)
def test_text_matches_itself(graph: Graph, text: str, expected: set[str]) -> None:
    """Metacharacters, quotes and backslashes match literally; case is still ignored."""
    assert _matches(graph, text) == expected


def test_dot_is_not_a_wildcard(graph: Graph) -> None:
    """``a.b`` must not match ``axb``, which it would as an unescaped pattern."""
    assert "axb" not in _matches(graph, "a.b")


def test_line_breaks_stay_inside_the_literal() -> None:
    """A newline in typed text cannot end the string literal early."""
    assert regex_literal("a\nb\rc") == "a\\nb\\rc"
