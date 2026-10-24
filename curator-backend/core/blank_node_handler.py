"""Blank-node handling utilities for the data-entry pipeline.

JSON-LD forms may produce blank nodes in two situations:
  1. The top-level entity has no `@id` (the user did not specify one).
  2. Nested helper-bridge nodes (e.g., AgentRole) are added inline with
     a temporary ``_:temp`` identifier.

Both cases are resolved before SHACL validation so the validator always
sees stable IRIs, and the same IRIs are written to Oxigraph.
"""

from __future__ import annotations

import re

# Whatever separates a namespace from a local name: "/" or "#" in a full IRI,
# ":" in a compact one (`rfdb:L111`).
_LOCAL_NAME_SEPARATOR = re.compile(r"[/#:]")


def _local_name(iri: str) -> str:
    """The part of an IRI or CURIE after its last separator.

    ``https://rosfeatr.eu/rdf/data/L111`` and ``rfdb:L111`` both give ``L111``.
    Nested references from the editor can arrive as CURIEs, and the prefix is
    whatever the loaded schema declares, so no prefix name is assumed here:
    stripping a literal ``rfdb:`` would mint ``…/data/kb:L111_hasAgentRole_0``
    for a schema using ``kb:``.
    """
    return _LOCAL_NAME_SEPARATOR.split(iri)[-1]


def skolemize(data: dict, parent_id: str, data_base: str) -> dict:
    """Recursively replace blank-node `@id` values with stable skolem IRIs.

    Only list-valued properties are recursed into; scalar values and JSON-LD
    keywords (keys starting with ``@``) are left untouched.

    Strategy: ``<data_base><parent_local>_<property_local>_<index>``, where
    ``data_base`` is the ``DATA_BASE_URI`` setting.

    Example::

        parent_id = "https://rosfeatr.eu/rdf/data/L111"
        property  = "core:hasAgentRole"
        index     = 0
        result    = "https://rosfeatr.eu/rdf/data/L111_hasAgentRole_0"
    """
    result = dict(data)
    local_parent = _local_name(parent_id)

    for key, value in result.items():
        if key.startswith("@"):
            continue
        if not isinstance(value, list):
            continue

        prop_local = key.split(":")[-1]
        new_list = []
        for i, item in enumerate(value):
            if isinstance(item, dict):
                item = dict(item)
                if item.get("@id", "").startswith("_:"):
                    item["@id"] = f"{data_base}{local_parent}_{prop_local}_{i}"
                # Recurse for nested structures
                item = skolemize(item, item.get("@id", parent_id), data_base)
            new_list.append(item)
        result[key] = new_list

    return result


def assign_entity_id(data: dict, shape_id: str, data_base: str) -> dict:
    """Assign a generated IRI when `@id` is absent or a blank-node stub.

    The IRI is ``data_base`` (the ``DATA_BASE_URI`` setting) plus the shape name
    and a random 8-hex-character suffix to keep it short and collision-resistant::

        shape_id  = "https://rosfeatr.eu/rdf/schema/PlaceShape"
        result    = "https://rosfeatr.eu/rdf/data/Place_4f3a9d12"

    If the entity already carries a non-blank `@id` it is returned unchanged.
    """
    if data.get("@id") and not data["@id"].startswith("_:"):
        return data
    import uuid

    short = shape_id.split("#")[-1].split("/")[-1].replace("Shape", "")
    result = dict(data)
    result["@id"] = f"{data_base}{short}_{uuid.uuid4().hex[:8]}"
    return result
