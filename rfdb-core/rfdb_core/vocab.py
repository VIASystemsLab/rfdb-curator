"""RDF vocabulary the digital-copy machinery owns.

The anchor shape plus the schema.org terms written for each file node. How a node
links to a parent (predicate + parent class) is resolved from the SHACL schema at
runtime — never hardcoded.

Split out of ``models/files.py`` because these are plain vocabulary constants, not
response models, and three separate layers need them: ``schema_extractor`` (which
lives here in rfdb-core), the curator's write path, and the reader's download and
file-stats routes. Keeping them in a service's ``models/`` package would have made
this shared library import from that service.
"""

SCHEMA_NS = "http://schema.org/"
SCHEMA_DIGITAL_DOCUMENT = SCHEMA_NS + "DigitalDocument"
SCHEMA_NAME = SCHEMA_NS + "name"
SCHEMA_ENCODING_FORMAT = SCHEMA_NS + "encodingFormat"
SCHEMA_CONTENT_URL = SCHEMA_NS + "contentUrl"
SCHEMA_CONTENT_SIZE = SCHEMA_NS + "contentSize"
SCHEMA_SHA256 = SCHEMA_NS + "sha256"
SCHEMA_NUMBER_OF_PAGES = SCHEMA_NS + "numberOfPages"

# Server-derived metadata terms: replaced (staged) or stripped (registered)
# by the write path in the curator's api/data.py — the payload's values are
# UI prefill only.
SCHEMA_DERIVED_TERMS = (
    SCHEMA_ENCODING_FORMAT,
    SCHEMA_CONTENT_URL,
    SCHEMA_CONTENT_SIZE,
    SCHEMA_SHA256,
    SCHEMA_NUMBER_OF_PAGES,
)
