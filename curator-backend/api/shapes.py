"""Shape metadata routes used to build the sidebar nav and form schemas.

`GET /shapes` (mounted under the curator's API prefix) is called once at startup
by the React app to populate the shape list. `GET /forms` is called each time the
user selects a shape, to retrieve the ordered field definitions that drive form
generation — a writer-only route, since form fields exist to drive an editing form.

The shape list is **identical** to the reader's: both build it through
:func:`rfdb_core.shapes.list_shapes`, so the `readOnly` flags cannot drift between
the two services and the editor can enumerate shapes with the writer down.
"""

from fastapi import APIRouter, HTTPException, Request

from core.config import settings
from rfdb_core.shapes import list_shapes as build_shape_list
from rfdb_core.shapes import stamp_read_only

router = APIRouter()


@router.get("/shapes")
def list_shapes(request: Request):
    """Return all SHACL NodeShapes with metadata.

    Each item includes the shape URI, human-readable label (from `rdfs:label`),
    description, CURIE and full-URI forms of `sh:targetClass`, a `shapeRole`
    classification, the list of property descriptors, and a `readOnly` flag
    derived from ``READ_ONLY_SHAPES``.
    """
    return build_shape_list(request.app.state.schema_extractor, settings.read_only_shapes)


@router.get("/forms")
def get_form_schema(shapeId: str, request: Request):
    """Return form field definitions for a SHACL shape.

    The `fields` array mirrors the `properties` list from the shapes route but is
    returned alongside the parent `shape` object so the form component has
    everything it needs in one round-trip.  Field `type` values map directly to
    React components: `lang-string` → language-tagged text input, `entity-search`
    → async autocomplete, `nested` → `AnonymousEntityEditor`, etc.
    The `shape` object includes a `readOnly` flag from ``READ_ONLY_SHAPES``.
    """
    shape = request.app.state.schema_extractor.get_shape(shapeId)
    if shape is None:
        raise HTTPException(status_code=404, detail=f"Shape '{shapeId}' not found")
    stamped = stamp_read_only(shape, settings.read_only_shapes)
    return {"shape": stamped, "fields": stamped["properties"]}
