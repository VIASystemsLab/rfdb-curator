"""FastAPI route handlers for the RossijskijFeatrDB data-entry API.

Every route that mutates the store is here, and only here. The read routes
(record listing, entity search, graph traversal, meta, digital-copy download)
are in ``dataexplorer-backend/api/``.

All of it sits under ``/api/v1/curator/``, a prefix that names the service owning
it, so a request to the wrong service fails legibly and a read-only deployment
simply omits the namespace. Nothing here is published under ``/rdf/``: that space holds
permanent identifiers, and those should not resolve to the tier that mints them.

Routers
-------
shapes    GET /api/v1/curator/shapes              shape metadata; *identical* to
                                                  the reader's, from one shared
                                                  implementation, so the editor
                                                  can list shapes from the
                                                  reader with the writer down
          GET /api/v1/curator/forms               field definitions driving the
                                                  editing form — writer-only,
                                                  since forms exist to edit
data      POST /api/v1/curator/entities           create/update, SHACL-validated
          DELETE /api/v1/curator/entities?id=…   delete
files     POST /api/v1/curator/files/staged       stage a PDF for a pending write
          GET  /api/v1/curator/files/staged/{id}  preview it before submit; only
                                                  the writer serves staged files
validate  POST /api/v1/curator/validate           dry-run SHACL validation
"""
