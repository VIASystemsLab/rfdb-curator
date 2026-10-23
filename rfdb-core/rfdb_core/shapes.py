"""The shape catalogue, as both services serve it.

The curator and the reader both answer a shapes request, and the two payloads
must be **identical**, from one code path, so the editor can list shapes with
the writer down. Both services call ``SchemaExtractor.get_all_shapes()`` over the
same schema and stamp ``readOnly`` here, so each can produce the flags the editor
needs to disable protected shapes.

``READ_ONLY_SHAPES`` is *policy metadata stating which shapes are editable*, which
any client needs in order to render a UI, not a write concern. It lives in
:class:`~rfdb_core.config.BaseServiceSettings` and both services pass it here.

``read_only_shapes`` is a parameter rather than a settings import so this module
stays framework- and service-agnostic, like the rest of the library.
"""

from __future__ import annotations

from collections.abc import Iterable


def stamp_read_only(shape: dict, read_only_shapes: Iterable[str]) -> dict:
    """Return a copy of ``shape`` with ``readOnly`` set from ``read_only_shapes``.

    Keeps ``SchemaExtractor`` a pure schema parser: whether a shape is editable is
    per-deployment policy, not schema. (Editability is not annotated in the
    schema because the same schema protects Glottolog languages in one
    environment and not another, which an annotation cannot express.)

    **This is the only place the key is produced.** A second implementation in
    either service would let the two catalogues' ``readOnly`` flags drift, which
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
