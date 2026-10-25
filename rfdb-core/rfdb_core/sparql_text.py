"""Put user-typed text into a SPARQL ``regex()`` filter so it matches literally.

The search routes match what a user types against labels and IRIs with
``regex(?x, "<text>", "i")``. Typed text is not a pattern: a ``(`` would make
the regex invalid, and the query would fail, while ``.`` would match any
character. Two escapes are needed, in this order:

1. the regex metacharacters (the XPath set SPARQL's ``regex`` uses, all of
   which Oxigraph's engine accepts escaped), so each one matches itself;
2. the SPARQL string literal (backslash, double quote, line breaks), so the
   result can sit between ``"…"`` in the query.
"""

from __future__ import annotations

_REGEX_META = set("\\|.-^?*+{}()[]$")

_LITERAL_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r"}


def regex_literal(text: str) -> str:
    """Return ``text`` as the body of a SPARQL string used as a literal regex.

    Args:
        text: What the user typed.

    Returns:
        A string to place between double quotes in a ``regex()`` call; it
        matches ``text`` exactly, character for character.
    """
    pattern = "".join(f"\\{c}" if c in _REGEX_META else c for c in text)
    return "".join(_LITERAL_ESCAPES.get(c, c) for c in pattern)
