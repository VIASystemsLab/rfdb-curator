"""The shape catalogue, as both services serve it.

Decision D11: the curator and the reader both answer a shapes request, and the
two payloads must be **identical**, from one code path. The catalogue itself was
already shared before this module — both services call
``SchemaExtractor.get_all_shapes()`` over the same schema — but the
``readOnly`` stamp was not. It lived in the curator's router and read a
curator-only setting, so the reader could not compute it.

That single divergence is C20: with the writer down, the editor had no shape list
and rendered an empty sidebar, because only the writer could produce the flags it
needs to disable protected shapes. The root cause was a misfiling —
``READ_ONLY_SHAPES`` was classified as a write concern, when it is *policy
metadata stating which shapes are editable*, which any client needs in order to
render a UI. It now lives in :class:`~rfdb_core.config.BaseServiceSettings` and
both services pass it here.

``read_only_shapes`` is a parameter rather than a settings import so this module
stays framework- and service-agnostic, like the rest of the library.
"""

from __future__ import annotations

from collections.abc import Iterable


def stamp_read_only(shape: dict, read_only_shapes: Iterable[str]) -> dict:
    """Return a copy of ``shape`` with ``readOnly`` set from ``read_only_shapes``.

    Keeps ``SchemaExtractor`` a pure schema parser: whether a shape is editable is
    per-deployment policy, not schema. (Annotating editability in the schema
    was considered and rejected — the same schema protects Glottolog languages in
    one environment and not another, which an annotation cannot express.)

    **This is the only place the key is produced.** A second implementation in
    either service would reintroduce C20 in a subtler form, which
    ``tests/core/test_shapes_stamp.py`` exists to prevent.
    """
    return {**shape, "readOnly": shape["id"] in set(read_only_shapes)}


def list_shapes(extractor, read_only_shapes: Iterable[str]) -> list[dict]:
    """The full stamped catalogue — what both services' shapes route returns.

    Args:
        extractor: A ``SchemaExtractor``.
        read_only_shapes: Shape ids to mark ``readOnly`` (``settings.read_only_shapes``).
    """
    flags = set(read_only_shapes)
    return [stamp_read_only(s, flags) for s in extractor.get_all_shapes()]
