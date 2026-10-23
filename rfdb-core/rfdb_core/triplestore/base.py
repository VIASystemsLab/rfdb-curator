"""The ``TripleStore`` seam — what both backends may assume of a triplestore.

Mirrors the ``file_storage`` seam that isolates Garage: routes and seeders
talk to this interface, never to a store's HTTP API, so another store (Fuseki/Jena,
GraphDB, Qlever, RDF4J, an embedded library, …) can be dropped in via the
``TRIPLESTORE`` setting.

Two things to know about the shape of this Protocol:

**The method names follow the Oxigraph client's, deliberately.** Renaming ``query``
to ``query_select`` and ``construct`` to ``query_construct`` would touch ~20 call
sites and the ~12 test modules that hand-roll fake clients, for no behavioural
gain. A naming review is queued in ``TODO.md`` for when a second implementation
exists to test the names against.

**The graph-scoping members are part of the contract, not an implementation
detail.** Route handlers build SPARQL strings *around* :meth:`from_clause` and
:meth:`with_clause`, and ``validation_merge._build_validation_construct`` takes a
``from_clause`` string as a parameter. A store whose scoping model differs must
still return usable clause strings (or empty ones, which callers already handle —
``/api/v1/dataexplorer/meta/graphs`` deliberately queries unscoped).

Read methods are used by both services; the write methods are the curator's alone,
which is what lets a reader be pointed at a read-only SPARQL endpoint.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rdflib import Graph


@runtime_checkable
class TripleStore(Protocol):
    """The operations a triplestore must support to back an RFDB service.

    ``runtime_checkable`` so the conformance suite can assert an implementation
    satisfies the seam. Note that this only verifies members exist, not their
    signatures — the contract tests cover behaviour.
    """

    # ------------------------------------------------------------------
    # Graph scoping — read and write
    # ------------------------------------------------------------------

    @property
    def graph(self) -> str | None:
        """The configured named graph URI, or ``None`` when using the default graph."""

    def from_clause(self) -> str:
        """Return a ``FROM <graph>`` clause, or ``""`` when no graph is configured."""

    def with_clause(self) -> str:
        """Return a ``WITH <graph>`` clause, or ``""`` when no graph is configured."""

    # ------------------------------------------------------------------
    # Read — used by both backends
    # ------------------------------------------------------------------

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        """Run a SELECT and return rows as ``{variable: value | None}`` dicts.

        Values are plain strings: datatype and language annotations are stripped.
        """

    def construct(self, sparql: str) -> Graph:
        """Run a CONSTRUCT (or DESCRIBE) and return the resulting rdflib graph."""

    def health(self) -> bool:
        """Return whether the store is reachable and answering."""

    # ------------------------------------------------------------------
    # Write — curator-backend only
    # ------------------------------------------------------------------

    def update(self, sparql: str) -> None:
        """Run a SPARQL UPDATE."""

    def load_turtle(self, turtle_data: str, graph_name: str | None = None) -> None:
        """Bulk-load Turtle, into ``graph_name`` when given, else the configured graph."""

    def clear_store(self) -> None:
        """Remove every triple from every graph. Destructive; used only by reset/seed."""
