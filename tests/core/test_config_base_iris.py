"""The base IRI settings: required, and refused without a trailing separator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rfdb_core.config import BaseServiceSettings


@pytest.mark.parametrize("base", ["https://example.org/data/", "https://example.org/data#"])
def test_base_iri_with_a_separator_is_accepted(base: str) -> None:
    """A base ending in ``/`` or ``#`` is taken as given."""
    settings = BaseServiceSettings(data_base_uri=base, schema_base_uri=base)
    assert settings.data_base_uri == base
    assert settings.schema_base_uri == base


@pytest.mark.parametrize("field", ["data_base_uri", "schema_base_uri"])
def test_base_iri_without_a_separator_is_refused(field: str) -> None:
    """A base without one would mint ``…/dataPlace_1``, so startup fails instead."""
    with pytest.raises(ValidationError, match="must end with"):
        BaseServiceSettings(**{field: "https://example.org/data"})


@pytest.mark.parametrize("variable", ["DATA_BASE_URI", "SCHEMA_BASE_URI"])
def test_base_iri_is_required(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    """A missing base fails at startup, naming the field, rather than defaulting."""
    monkeypatch.delenv(variable)
    with pytest.raises(ValidationError, match=variable.lower()):
        BaseServiceSettings(_env_file=None)
