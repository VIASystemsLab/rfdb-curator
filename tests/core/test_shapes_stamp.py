"""The ``readOnly`` stamp is single-sourced.

Both services serve a shape catalogue, and the two payloads must be identical,
``readOnly`` flags included, so the editor can take the catalogue from the reader
and keep working with the writer down. The catalogue is shared through one
``SchemaExtractor`` over one ``rfdb_schema.ttl``; the *flag* must be shared too, or
the editor depends on the writer for it and shows an empty sidebar whenever the
writer is down.

The invariant is maintained by sharing the implementation, not by two copies that
agree today. So this module tests the shared function's behaviour, and then asserts
that neither service builds the key itself. That second half is the part a route
test cannot cover: a re-implementation would pass every endpoint test right up until
the day the two drifted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rfdb_core.shapes import list_shapes, stamp_read_only

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "rfdb_schema.ttl"

LOCKED = "https://rosfeatr.eu/rdf/schema/LanguageShape"
OPEN = "https://rosfeatr.eu/rdf/schema/SourceShape"


# ---------------------------------------------------------------------------
# The stamp itself
# ---------------------------------------------------------------------------


def test_listed_shape_is_flagged_read_only() -> None:
    assert stamp_read_only({"id": LOCKED}, [LOCKED])["readOnly"] is True


def test_unlisted_shape_is_flagged_writable() -> None:
    assert stamp_read_only({"id": OPEN}, [LOCKED])["readOnly"] is False


def test_flag_is_always_present_even_with_no_policy() -> None:
    """An empty policy means "everything is editable", not "no flag".

    A missing key and ``False`` are different things to a client: the first forces
    it to guess. This is what makes the two services' payloads structurally
    identical regardless of configuration.
    """
    stamped = stamp_read_only({"id": OPEN}, [])
    assert stamped["readOnly"] is False


def test_stamp_does_not_mutate_its_input() -> None:
    """The extractor caches shape dicts, so mutating one would poison later reads."""
    shape = {"id": LOCKED, "label": "Language"}
    stamp_read_only(shape, [LOCKED])
    assert "readOnly" not in shape


def test_stamp_preserves_every_other_field() -> None:
    shape = {"id": OPEN, "label": "Source", "properties": [{"path": "p"}]}
    stamped = stamp_read_only(shape, [LOCKED])
    assert {k: v for k, v in stamped.items() if k != "readOnly"} == shape


# ---------------------------------------------------------------------------
# The catalogue builder
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not SCHEMA_PATH.exists(), reason="rfdb_schema.ttl not found")
def test_list_shapes_stamps_every_shape() -> None:
    """What both services return: the whole catalogue, every entry flagged."""
    from rfdb_core.schema_extractor import SchemaExtractor

    shapes = list_shapes(SchemaExtractor(str(SCHEMA_PATH)), [LOCKED])
    assert shapes, "the schema defines shapes"
    assert all("readOnly" in s for s in shapes)
    # And the policy is actually applied, not just defaulted.
    assert [s["id"] for s in shapes if s["readOnly"]] == [LOCKED]


# ---------------------------------------------------------------------------
# Neither service may re-implement it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["curator-backend", "dataexplorer-backend"])
def test_service_delegates_instead_of_building_the_key(service: str) -> None:
    """No service may produce ``readOnly`` itself — it must call this module.

    Checked on source text rather than behaviour on purpose: two independent
    implementations agreeing is exactly the state that looks correct and is not.
    """
    source = (REPO_ROOT / service / "api" / "shapes.py").read_text()
    assert "rfdb_core.shapes" in source, f"{service} does not import the shared stamp"

    # Ignore the module docstring, which legitimately discusses the key by name.
    body = source.split('"""')[-1]
    assert '"readOnly"' not in body and "'readOnly'" not in body, (
        f"{service} constructs the readOnly key locally instead of delegating"
    )
