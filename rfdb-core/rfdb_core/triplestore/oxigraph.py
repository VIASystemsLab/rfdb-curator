"""Oxigraph implementation of the :class:`~rfdb_core.triplestore.base.TripleStore` seam.

All communication uses the Oxigraph HTTP API:
  - /query  — SPARQL SELECT and CONSTRUCT (read)
  - /update — SPARQL UPDATE (writes, and store management: DROP ALL, CLEAR DEFAULT)
  - /store  — Graph Store Protocol (bulk load)

Oxigraph is intentionally schema-free; all shape constraints are enforced
by ``ShaclValidator`` in Python before any data reaches this layer.
"""

from __future__ import annotations

import logging

import httpx
from rdflib import Graph
from rdflib.exceptions import Error as RdflibError
from rdflib.plugins.parsers.notation3 import BadSyntax

logger = logging.getLogger(__name__)


class OxigraphStore:
    """Thin HTTP client for the Oxigraph SPARQL 1.1 endpoint.

    The first (and currently only) implementation of :class:`TripleStore`. Built via
    :func:`rfdb_core.triplestore.build_triplestore` at startup and stored on
    ``app.state.store``; all API routes pull it from there.

    Reads use only SPARQL 1.1, so a store-agnostic reader can point at any compliant
    endpoint. The writes (``update``, ``load_turtle``, ``clear_store``) additionally
    rely on the Graph Store Protocol.

    Named-graph scoping: queries are sent as written, so a read is scoped to the
    configured graph only when the caller includes :meth:`from_clause` (and an
    update only via :meth:`with_clause`). :meth:`load_turtle`, by contrast,
    targets ``data_graph_uri`` by default, via ``?graph=<uri>`` on the Graph
    Store Protocol rather than ``?default``.
    """

    def __init__(
        self,
        base_url: str,
        data_graph_uri: str | None = None,
        load_timeout: float = 300.0,
    ) -> None:
        """Initialise the client.

        Args:
            base_url: Root URL of the Oxigraph HTTP server, e.g.
                ``http://localhost:7878``.  A trailing slash is stripped
                automatically so callers need not be careful about it.
            data_graph_uri: Named graph URI used for all read and write
                operations.  When ``None`` all operations target the default
                graph.  In normal application use this is always set from
                ``settings.data_graph_uri``.
            load_timeout: Read timeout in seconds for :meth:`load_turtle`.
                Bulk loads block until Oxigraph has parsed *and* indexed the
                whole document, so large vocabulary files (tens of MB) can take
                well over a minute.  Defaults to 300s; set from
                ``settings.oxigraph_load_timeout`` in normal application use.
        """
        self.base_url = base_url.rstrip("/")
        self.data_graph_uri = data_graph_uri
        self.load_timeout = load_timeout

    @property
    def graph(self) -> str | None:
        """The configured named graph URI, or ``None`` when using the default graph.

        This is a plain read-only accessor for ``data_graph_uri``.  Use
        :meth:`from_clause` and :meth:`with_clause` to get ready-made SPARQL
        clause strings rather than interpolating this value manually.
        """
        return self.data_graph_uri

    def from_clause(self) -> str:
        """Return a ``FROM <graph>`` clause, or an empty string when no graph is set."""
        return f"FROM <{self.data_graph_uri}>" if self.data_graph_uri else ""

    def with_clause(self) -> str:
        """Return a ``WITH <graph>`` clause, or an empty string when no graph is set."""
        return f"WITH <{self.data_graph_uri}>" if self.data_graph_uri else ""

    # ------------------------------------------------------------------
    # SPARQL read
    # ------------------------------------------------------------------

    def query(self, sparql: str) -> list[dict[str, str | None]]:
        """Run a SELECT query and return rows as plain dicts.

        Each dict maps variable name → string value (or ``None`` when unbound).
        Only the ``value`` field from the SPARQL JSON response is kept; RDF
        datatype and language annotations are stripped — callers that need
        them should use :meth:`construct` instead.

        This method is a pure transport: the SPARQL string is sent exactly as
        provided.  Callers should prepend ``from_clause()`` when they want to
        scope a read to the configured named graph.

        Args:
            sparql: A valid SPARQL 1.1 SELECT query string.

        Returns:
            A list of row dicts, one per solution in the result set.  Each dict
            maps every projected variable name to its string value, or ``None``
            when the variable is unbound in that solution.

        Raises:
            httpx.HTTPStatusError: If Oxigraph returns a non-2xx HTTP status.
            ValueError: If the response body is not valid JSON or does not
                conform to the SPARQL 1.1 Query Results JSON Format
                (missing ``head.vars`` or ``results.bindings``).
        """
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/query",
                data={"query": sparql},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp.raise_for_status()

        try:
            result = resp.json()
            variables = result["head"]["vars"]
            return [
                {v: binding.get(v, {}).get("value") for v in variables}
                for binding in result["results"]["bindings"]
            ]
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Unexpected SPARQL JSON response from Oxigraph: {exc}\n"
                f"Response body (first 500 chars): {resp.text[:500]}"
            ) from exc

    def construct(self, sparql: str) -> Graph:
        """Run a CONSTRUCT query and return a parsed rdflib ``Graph``.

        Used during validation to fetch the triples of entities already in the
        store so that SHACL ``sh:class`` / ``sh:node`` checks can resolve cross-
        entity references (e.g., a Holding Organization pointing to a Place).
        Returns an empty ``Graph`` when the store returns no triples.

        This method is a pure transport: the SPARQL string is sent exactly as
        provided.  Callers should prepend ``from_clause()`` when they want to
        scope a CONSTRUCT to the configured named graph.

        Args:
            sparql: A valid SPARQL 1.1 CONSTRUCT query string.

        Returns:
            An rdflib ``Graph`` populated with the returned triples, or an
            empty ``Graph`` when the store has no matching triples.

        Raises:
            httpx.HTTPStatusError: If Oxigraph returns a non-2xx HTTP status.
            ValueError: If the response body cannot be parsed as Turtle.
        """
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/query",
                data={"query": sparql},
                headers={"Accept": "text/turtle"},
            )
            resp.raise_for_status()

        graph = Graph()
        if resp.text.strip():
            try:
                graph.parse(data=resp.text, format="turtle")
            # BadSyntax derives from SyntaxError, NOT from rdflib.exceptions.Error,
            # so catching the latter alone would let the most likely failure, a
            # malformed Turtle body, escape as a bare BadSyntax whose message is
            # "<no detail available>", losing the response excerpt below. Both
            # are needed.
            except (RdflibError, BadSyntax) as exc:
                raise ValueError(
                    f"Oxigraph returned invalid Turtle for CONSTRUCT query: {exc}\n"
                    f"Response body (first 500 chars): {resp.text[:500]}"
                ) from exc
        return graph

    # ------------------------------------------------------------------
    # SPARQL write
    # ------------------------------------------------------------------

    def update(self, sparql: str) -> None:
        """Execute a SPARQL UPDATE statement (INSERT, DELETE, …).

        This method is a pure transport: the SPARQL string is sent exactly as
        provided.  Callers who want their updates scoped to a named graph
        should prepend ``with_clause()`` to the SPARQL string.

        Args:
            sparql: A valid SPARQL 1.1 Update string.

        Raises:
            httpx.HTTPStatusError: If Oxigraph returns a non-2xx HTTP status.
        """
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/update",
                data={"update": sparql},
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Graph store (bulk load)
    # ------------------------------------------------------------------

    def load_turtle(self, turtle_data: str, graph_name: str | None = None) -> None:
        """Bulk-load a Turtle document into Oxigraph via the Graph Store Protocol.

        The target graph is resolved in this order:

        1. ``graph_name`` argument (explicit override for this call).
        2. ``self.data_graph_uri`` (the configured named graph; the normal case).
        3. The Oxigraph default graph (fallback when neither is set).

        Oxigraph's Graph Store endpoint requires ``?default`` to write to the
        default graph and ``?graph=<uri>`` for a named graph.  Omitting both
        causes Oxigraph to auto-create a fresh unnamed graph, breaking reads.

        Args:
            turtle_data: A valid Turtle-serialised RDF document as a string.
                Must be UTF-8 text; the string is encoded to bytes before
                transmission and the ``Content-Type`` is declared as
                ``text/turtle; charset=utf-8`` per RFC 7230 and the Turtle spec.
            graph_name: Optional named graph URI that overrides
                ``self.data_graph_uri`` for this single call.

        Raises:
            httpx.HTTPStatusError: If Oxigraph returns a non-2xx HTTP status.
        """
        target = graph_name or self.data_graph_uri
        if target:
            params: dict[str, str] = {"graph": target}
        else:
            # Oxigraph Graph Store protocol requires `?default` to write to the
            # default graph. Without it, POST /store creates a fresh named graph.
            params = {"default": ""}
        # Oxigraph does not send response headers until it has parsed and
        # indexed the entire document, so the read timeout must cover the whole
        # bulk load. Large vocab files (e.g. the ~38 MB Glottolog language list)
        # routinely exceed 60s, so the read timeout is the configurable
        # `load_timeout`.
        timeout = httpx.Timeout(self.load_timeout, connect=10.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self.base_url}/store",
                content=turtle_data.encode("utf-8"),
                headers={"Content-Type": "text/turtle; charset=utf-8"},
                params=params,
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    def clear_store(self) -> None:
        """Erase every triple in the store — named graphs and the default graph.

        This is a **destructive, irreversible operation**.  It is intended
        exclusively for startup resets (when ``reset_data_on_startup`` is
        ``true`` in the application settings) and for test-suite teardown.
        It must never be called in production without an explicit, deliberate
        configuration flag.

        Two SPARQL UPDATE statements are issued in sequence:

        1. ``DROP ALL`` — drops every named graph together with the triples
           it contains.  On Oxigraph this does *not* affect the default graph.
        2. ``CLEAR DEFAULT`` — removes all triples from the default graph,
           leaving an empty but still-addressable default dataset.

        Both statements are sent as separate requests because Oxigraph does not
        support semicolon-separated UPDATE sequences in a single request body.

        Raises:
            httpx.HTTPStatusError: If ``DROP ALL`` returns a non-2xx status.
                The store is left completely unchanged in this case.
            RuntimeError: If ``DROP ALL`` succeeded but ``CLEAR DEFAULT``
                failed.  Named graphs have already been removed; the default
                graph may still contain triples.  Inspect Oxigraph before
                retrying.
        """
        try:
            self.update("DROP ALL")
        except httpx.HTTPStatusError as exc:
            raise httpx.HTTPStatusError(
                "clear_store: DROP ALL failed — store is unchanged.",
                request=exc.request,
                response=exc.response,
            ) from exc

        try:
            self.update("CLEAR DEFAULT")
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "clear_store: DROP ALL succeeded but CLEAR DEFAULT failed. "
                "Named graphs have been removed; the default graph may still "
                "contain triples.  Inspect Oxigraph before retrying."
            ) from exc

    # ------------------------------------------------------------------
    # Healthcheck
    # ------------------------------------------------------------------

    def health(self) -> bool:
        """Return ``True`` when Oxigraph is reachable and responding.

        Called by the ``/health`` endpoint to surface store availability in
        Docker health-checks and the UI status bar.  Transport errors and
        timeouts are caught and logged at WARNING level so operators can
        see connectivity problems in the application log without the
        health probe itself raising an exception.

        Returns:
            ``True`` if the store responds with any HTTP status below 500;
            ``False`` on any transport error or timeout.
        """
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.base_url}/")
                return resp.status_code < 500
        except httpx.TransportError as exc:
            logger.warning("Oxigraph health check failed: %s", exc)
            return False
