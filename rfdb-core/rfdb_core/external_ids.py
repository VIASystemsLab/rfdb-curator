"""Links for external identifiers stored as bare text.

A schema can store an authority's identifier as plain text, the way Wikidata
does (a VIAF ID ``"293925180"``, a RISM ID ``"sources/990052516"``), and leave
building the link to the application. The ``EXTERNAL_ID_LINKS`` setting maps
each such predicate to a URL template with ``$1`` where the identifier goes,
following Wikidata's formatter URLs (P1630).
"""

from __future__ import annotations

from urllib.parse import quote

PLACEHOLDER = "$1"


def external_id_url(template: str, identifier: str) -> str:
    """Fill ``template`` with ``identifier``, percent-encoded.

    ``/`` is kept as is: some identifiers are paths (RISM's
    ``sources/990052516``) and the template expects them unencoded.

    Example::

        >>> external_id_url("https://rism.online/$1", "sources/990052516")
        'https://rism.online/sources/990052516'
    """
    return template.replace(PLACEHOLDER, quote(identifier.strip(), safe="/"))
