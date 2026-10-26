"""The record search's alternative labels come from SEARCH_LABEL_PREDICATES, not code."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rfdb_core.config import BaseServiceSettings

SKOS_ALT_LABEL = "http://www.w3.org/2004/02/skos/core#altLabel"
SCHEMA_ALTERNATE_NAME = "http://schema.org/alternateName"


def test_default_is_skos_alt_label() -> None:
    """Unset, the search also matches skos:altLabel, the rfdb schema's alternative label."""
    assert BaseServiceSettings().search_label_predicates == [SKOS_ALT_LABEL]


def test_setting_parses_a_json_array_and_an_empty_value() -> None:
    """The variable is a JSON array string; an empty one means the label and IRI only."""
    parsed = BaseServiceSettings(search_label_predicates=f'["{SCHEMA_ALTERNATE_NAME}"]')
    assert parsed.search_label_predicates == [SCHEMA_ALTERNATE_NAME]
    assert BaseServiceSettings(search_label_predicates="").search_label_predicates == []


@pytest.mark.parametrize("iri", ["skos:altLabel", "http://example.org/a> } DROP ALL #"])
def test_an_iri_unsafe_in_sparql_is_refused(iri: str) -> None:
    """The IRIs go into the search query, so a CURIE or a SPARQL-breaking IRI is refused."""
    with pytest.raises(ValidationError, match="search_label_predicates"):
        BaseServiceSettings(search_label_predicates=[iri])
