"""Shape metadata for read clients: the sidebar nav and the graph type picker.

**Identical to the curator's shapes response** (decision D11). Both services build
it with :func:`rfdb_core.shapes.list_shapes` over the same schema, so there
is one implementation and one place to change.

This reverses D3, which had the reader omit the ``readOnly`` flag on the grounds
that ``READ_ONLY_SHAPES`` was a write-policy setting a read service had no business
knowing. The flag does gate writes — but it also states which shapes are editable,
which is exactly what a client needs to render a UI. Withholding it meant the
editor had to fetch its shape list from the writer, so with the writer down it had
no sidebar at all (C20). Serving the same catalogue from both services removes the
divergence instead of teaching the client to work around it.

There is still no forms route here: form field definitions exist to drive an
editing form.
"""

from fastapi import APIRouter, Request

from core.config import settings
from rfdb_core.shapes import list_shapes as build_shape_list

router = APIRouter()


@router.get("/shapes")
def list_shapes(request: Request):
    """Return all SHACL NodeShapes with metadata.

    Each item includes the shape URI, human-readable label (from `rdfs:label`),
    description, CURIE and full-URI forms of `sh:targetClass`, a `shapeRole`
    classification, the list of property descriptors, and a `readOnly` flag
    derived from ``READ_ONLY_SHAPES``.

    Byte-identical to the curator's response — asserted by
    ``tests/core/test_shapes_stamp.py`` on the shared implementation.
    """
    return build_shape_list(request.app.state.schema_extractor, settings.read_only_shapes)
