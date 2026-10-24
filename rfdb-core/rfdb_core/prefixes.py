"""The project's CURIE prefix map, read from the schema's ``sh:declare`` declarations.

Served by ``GET /api/v1/dataexplorer/meta/prefixes`` (the frontends hydrate it for
IRI compaction and the Data Context Panel) and used by the ``/rdf/`` pages. It is
read from ``sh:declare`` rather than from the rdflib graph's namespaces on purpose:

  - a bare ``rdflib.Graph()`` pre-binds ~29 unrelated "well-known" vocabularies
    (``brick``, ``csvw``, ``dcat``, ``qb``, ``odrl``, …) that would leak into the map,
  - ``@prefix`` lines are Turtle syntax and do not survive parsing, while
    ``sh:declare`` triples do, and
  - the schema declares the prefixes of the data files too, so one source covers
    every TTL file the project loads.

``scan_ttl_prefixes`` powers the drift check (``curator-backend/scripts/
check_prefixes.py``): every ``@prefix`` in the schema and data files must be declared.
"""

import re
from collections.abc import Iterable

from rdflib import Graph
from rdflib.namespace import SH


def declared_prefixes(graph: Graph) -> dict[str, str]:
    """Collect the ``sh:declare`` prefix declarations of a schema graph.

    Reads every ``[] sh:declare [ sh:prefix "p" ; sh:namespace "ns"^^xsd:anyURI ]``,
    the SHACL-native prefix map, wherever it hangs. A declaration missing either
    half is skipped. The same prefix declared twice with the same namespace is
    fine; with two namespaces it is an error, because no answer would be right.

    Example:
        >>> declared_prefixes(SchemaExtractor("schema/rfdb_schema.ttl").graph)["rfdb"]
        'https://rosfeatr.eu/rdf/data/'

    Raises:
        ValueError: a prefix is declared with two different namespaces.
    """
    found: dict[str, str] = {}
    for declaration in graph.objects(None, SH.declare):
        prefix = graph.value(declaration, SH.prefix)
        namespace = graph.value(declaration, SH.namespace)
        if prefix is None or namespace is None:
            continue
        prefix, namespace = str(prefix), str(namespace)
        if found.get(prefix, namespace) != namespace:
            raise ValueError(
                f"Prefix '{prefix}' is declared with two namespaces: "
                f"{found[prefix]} and {namespace}"
            )
        found[prefix] = namespace
    return dict(sorted(found.items()))


# Matches a Turtle ``@prefix name: <uri> .`` or SPARQL ``PREFIX name: <uri>`` line.
# The empty (base) prefix ``@prefix : <…>`` is intentionally NOT matched.
_PREFIX_RE = re.compile(
    r"^\s*(?:@prefix|PREFIX)\s+([A-Za-z][\w.\-]*):\s*<([^>]*)>\s*\.?\s*$",
    re.IGNORECASE,
)


def scan_ttl_prefixes(paths: Iterable[str]) -> dict[str, str]:
    """Collect ``@prefix``/``PREFIX`` declarations from the header of each TTL file.

    Reads only the leading directive block and stops at the first triple line, so
    it stays cheap even on very large files (e.g. the ~38 MB Glottolog dump). On a
    prefix conflict, later files win. Unreadable paths are skipped silently.

    Example:
        >>> scan_ttl_prefixes(["schema/rfdb_schema.ttl"])["rfdb"]
        'https://rosfeatr.eu/rdf/data/'
    """
    found: dict[str, str] = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    match = _PREFIX_RE.match(line)
                    if match:
                        found[match.group(1)] = match.group(2)
                        continue
                    # Ignore @base and any malformed directive; stop at the first
                    # line that is neither blank/comment nor a directive (a triple).
                    low = stripped.lower()
                    if not low.startswith(("@prefix", "prefix", "@base")):
                        break
        except OSError:
            continue
    return found
