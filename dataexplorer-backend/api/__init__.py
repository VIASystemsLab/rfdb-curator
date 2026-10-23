"""FastAPI route handlers for the RossijskijFeatrDB read API.

Every route here is a GET. There is no write path, no SHACL validator and no
seeder in this service — see ``app.py``.

This service owns **two** URL spaces, and the split is the point: ``/rdf/`` holds
public, permanent identifiers, while ``/api/v1/dataexplorer/`` is our own apps'
operational surface. Only the second may be reshaped in a future version.

The data space — stable, unversioned, publicly dereferenceable
---------------------------------------------------------------
resource  GET /rdf/data/{id}                       conneg dereference: turtle,
                                                   json-ld, rdf+xml, n-triples
files     GET /rdf/data/{id}/content               digital-copy bytes; this is
                                                   what ``schema:contentUrl``
                                                   points at
resource  GET /rdf/schema/{ShapeName}              a SHACL shape's own definition

The operational surface — versioned, ours
------------------------------------------
entities  GET /api/v1/dataexplorer/entities/search  autocomplete for relation
                                                    fields
data      GET /api/v1/dataexplorer/entities         record listing
          GET …/entities/counts                     per-shape counts
          GET …/entities/get?id=<iri>               the editor's triple list
shapes    GET /api/v1/dataexplorer/shapes           shape metadata, *identical* to
                                                    the curator's, so the editor
                                                    works with the writer down
graph     GET /api/v1/dataexplorer/graph/node       schema-aware traversal
meta      GET /api/v1/dataexplorer/meta/prefixes    prefix map
          GET …/meta/graphs, …/meta/files           graph + storage stats

An entity IRI always travels as ``?id=``, never as a path segment — matching
``/graph/node``. A path parameter would have to be greedy (an encoded IRI's ``%2F``
is decoded before matching), and a greedy parameter under ``/entities/`` swallows
its literal siblings unless registration order happens to save it. Router include
order is therefore not load-bearing, and a test asserts no route uses ``:path``.
"""
