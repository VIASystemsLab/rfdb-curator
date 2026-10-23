"""Shared library for the RossijskijFeatrDB services.

Holds everything both the curator (writer) and dataexplorer (reader) backends
need: the ``TripleStore`` seam, the curated CURIE map, SHACL schema extraction,
the object-storage seam, shared settings, and structured logging.

Intentionally free of FastAPI so neither service inherits the other's web
stack. Nothing here performs HTTP request handling.
"""

from importlib.metadata import version as _version

# The repository carries one version (see the versioning rule), so this
# package's is the release both services report.
__version__ = _version("rfdb-core")
