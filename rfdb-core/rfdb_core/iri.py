"""IRI validation for anything that gets interpolated into SPARQL text.

Both services take full IRIs straight from the client — path parameters, query
strings, JSON-LD ``@id`` values, predicate URIs from an update payload — and
splice them into query strings inside ``<…>``. Any character that could close
that bracket or start a new clause has to be rejected before the splice. This
module is that check, in one place, used by the curator's ``api/data.py`` and the
reader's ``api/graph.py``.

Returns a reason string rather than raising an HTTP error: rfdb-core is
framework-agnostic, and choosing a status code is the service's job. Callers
keep a three-line adapter next to their other handlers::

    def _validate_iri(iri: str) -> None:
        if reason := iri_error(iri):
            raise HTTPException(status_code=400, detail=reason)

This is deliberately a syntactic guard, not IRI parsing (RFC 3987). It is the
last line of defence for query construction, so it errs toward refusing input
that merely *looks* dangerous.
"""

from __future__ import annotations

import re

# Characters that would terminate the enclosing <…> or begin a new SPARQL
# clause. Whitespace is in the set because a newline alone splits one query
# into two.
_IRI_UNSAFE = re.compile(r'[<>"{}|\\^`\s]')


def iri_error(iri: str) -> str | None:
    """Return why ``iri`` is unsafe to interpolate into SPARQL, or ``None`` if it is fine.

    Args:
        iri: The candidate IRI, exactly as received from the client.

    Returns:
        A message suitable for a 400 response body, or ``None`` when ``iri``
        passes both checks (http(s) scheme, no SPARQL-hostile characters).

    Example::

        >>> iri_error("https://rosfeatr.eu/rdf/data/L111") is None
        True
        >>> iri_error("urn:isbn:123")
        'Invalid IRI: urn:isbn:123'
    """
    if not (iri.startswith("http://") or iri.startswith("https://")):
        return f"Invalid IRI: {iri}"
    if _IRI_UNSAFE.search(iri):
        return f"IRI contains unsafe characters: {iri}"
    return None
