"""Settings shared by every RossijskijFeatrDB service.

Holds only what more than one service needs: how to reach the triplestore, where
the SHACL schema lives, the base IRIs records and shapes are named under, CORS,
logging, and the object-storage credentials. Each
service instantiates a module-level ``settings`` singleton of its own: the curator
from a subclass adding its seeding/reset/read-only knobs, the reader from
:class:`BaseServiceSettings` directly, since it needs nothing more.

Values come exclusively from environment variables or a ``.env`` file; no
deployment-specific defaults are hardcoded. Fields with no default here are
**required** and raise a ``ValidationError`` at startup naming the missing
variable. The ``.env`` path is resolved relative to the working directory, which
is each service's own root.
"""

from __future__ import annotations

import json

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Configuration common to the curator (writer) and dataexplorer (reader).

    Attributes:
        oxigraph_url: Base URL of the triplestore's HTTP endpoint, no trailing
            slash. Example: ``http://localhost:7878``
        data_graph_uri: Named graph URI scoping reads (``FROM <uri>``) and Turtle
            loads (``?graph=<uri>``). Example: ``https://rosfeatr.eu/rdf/graph/``
        schema_path: Path to the SHACL schema Turtle file, relative to the
            service's working directory. Example: ``schema/schema.ttl``
        data_base_uri: Namespace every minted record and digital copy is named
            under. Example: ``https://rosfeatr.eu/rdf/data/``
        schema_base_uri: Namespace of the schema's SHACL shapes. Example:
            ``https://rosfeatr.eu/rdf/schema/``
        cors_origins: Allowed CORS origins, supplied as a JSON array string and
            parsed by :meth:`parse_cors`. Both compose files give both services
            the same value.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ------------------------------------------------------------------ #
    # Triplestore / SPARQL                                                 #
    # ------------------------------------------------------------------ #

    triplestore: str = "oxigraph"
    """Which :class:`~rfdb_core.triplestore.base.TripleStore` implementation to build.

    Selects the store at deploy time via ``TRIPLESTORE``; ``build_triplestore``
    raises on an unrecognised value rather than defaulting silently. Only
    ``oxigraph`` exists today — the seam is what makes adding another a new module
    plus one registry entry, with no call-site changes."""

    oxigraph_url: AnyHttpUrl
    """Base URL of the Oxigraph HTTP endpoint.  ``AnyHttpUrl`` accepts both
    ``http://`` and ``https://`` schemes, so no union type is required."""

    data_graph_uri: str
    """Named graph URI used for all SPARQL reads (``FROM <uri>``) and
    Graph Store Protocol writes (``?graph=<uri>``)."""

    oxigraph_load_timeout: float = 300.0
    """Read timeout in seconds for bulk Turtle loads (``load_turtle``).

    Oxigraph withholds response headers until a document is fully parsed and
    indexed, so seeding large vocabulary files (e.g. the ~38 MB Glottolog
    language list) can take well over a minute.  Defaults to 300s; raise it
    further via ``OXIGRAPH_LOAD_TIMEOUT`` if seeding very large files.

    Only the writer bulk-loads today, but the field stays here with the rest of
    the store configuration so a reader pointed at a slow endpoint can tune it."""

    # ------------------------------------------------------------------ #
    # Schema                                                               #
    # ------------------------------------------------------------------ #

    schema_path: str
    """Path to the SHACL schema Turtle file (e.g. ``schema/schema.ttl``).

    Both services parse it: the curator for validation and form schemas, the
    reader for relation predicates and type/relation labels."""

    # ------------------------------------------------------------------ #
    # Base IRIs                                                            #
    # ------------------------------------------------------------------ #

    data_base_uri: str
    """Namespace every record this deployment mints is named under, digital-copy
    nodes included: ``<data_base_uri>Place_ab12cd34``, and a copy's
    ``schema:contentUrl`` is ``<data_base_uri>File_x/content``. Both services slice
    it off an IRI to recover a local id, and the reader serves the IRIs under it
    on ``/rdf/data/{local_name}``."""

    schema_base_uri: str
    """Namespace of the schema's SHACL shapes (the ``rfdbs:`` prefix in the rfdb
    schema). The reader serves the shapes under it on ``/rdf/schema/{name}``.
    Must match the namespace the loaded schema names its shapes in."""

    read_only_shapes: list[str] = []
    """Shape URIs whose records are not editable in this deployment.

    Supplied as a JSON array string via ``READ_ONLY_SHAPES``, e.g.
    ``'["https://rosfeatr.eu/rdf/schema/LanguageShape"]'``. Shapes listed here get
    ``"readOnly": true`` in every shapes response, and the curator additionally
    returns 403 on writes targeting them. Defaults to ``[]``.

    **Shared, not writer-only** (D11), and the reclassification matters: this looks
    like a write concern because the curator enforces it, but it is *policy
    metadata stating which shapes are editable* — which any client needs to render
    a UI. Filing it under the curator is what caused C20, where the editor could
    not list shapes at all with the writer down. Both services now receive it and
    stamp the flag through the one implementation in
    :mod:`rfdb_core.shapes`, so the two catalogues cannot drift.

    Operational caveat: "identical" now rests on both services being given the
    same value. Supply it from a single YAML anchor in Compose rather than two
    literals that can be edited apart."""

    # ------------------------------------------------------------------ #
    # HTTP / CORS                                                          #
    # ------------------------------------------------------------------ #

    cors_origins: list[str]
    """Allowed CORS origins.  Parsed from a JSON array string by
    :meth:`parse_cors` so it works transparently in both ``.env`` files
    and Docker Compose ``environment:`` blocks."""

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #

    log_file: str = "logs/app.jsonl"
    """Path to the structured JSON-lines log file.
    Parent directories are created automatically at startup.
    Default: ``logs/app.jsonl`` (relative to the service's working directory)."""

    log_level: str = "INFO"
    """Minimum log level for both the file and console handlers.
    Accepted values: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``."""

    truncate_log_on_startup: bool = False
    """When ``true``, truncate ``log_file`` on service process startup.
    Useful in local Docker workflows where old JSON-lines logs should not
    accumulate across rebuilds/restarts."""

    truncate_log_on_fresh_container_start: bool = False
    """When ``true``, truncate ``log_file`` only on first startup of a
    freshly created container (for example after ``docker compose up --build``).
    Subsequent restarts of the same container keep appending to the file."""

    # ------------------------------------------------------------------ #
    # Object storage (Garage / S3) — source digital copies                 #
    # ------------------------------------------------------------------ #
    # Shared: the curator uploads and promotes blobs, the reader streams them back
    # for GET /rdf/data/{id}/content and counts them for the meta files route.
    # Satisfies rfdb_core.file_storage.S3Settings structurally.

    s3_endpoint: str = ""
    """S3 API endpoint of the Garage service, e.g. ``http://garage:3900``
    (Docker-internal) or ``http://localhost:3900`` (host). Empty disables
    file storage; the upload routes then return 503."""

    s3_region: str = "garage"
    """S3 region name. Garage uses a fixed pseudo-region (``garage`` by
    default, matching ``s3_region`` in ``garage.toml``)."""

    s3_bucket: str = "sources"
    """Bucket that holds all source PDF objects. Bootstrapped by
    ``scripts/garage-init.sh``."""

    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    """Predefined S3 credentials (same values Garage imports via
    ``garage key import``). Empty by default so non-storage deployments and
    the test suite start without them; the storage client validates presence
    lazily on first use."""

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("data_base_uri", "schema_base_uri")
    @classmethod
    def check_namespace(cls, v: str) -> str:
        """Require a base IRI to end in ``/`` or ``#``.

        IRIs are built by appending a local name to the base, so a base without a
        separator would mint ``…/dataPlace_1`` and fail no check along the way.
        """
        if not v.endswith(("/", "#")):
            raise ValueError(f"must end with '/' or '#', got {v!r}")
        return v

    @field_validator("read_only_shapes", mode="before")
    @classmethod
    def parse_read_only_shapes(cls, v: str | list[str]) -> list[str]:
        """Accept ``READ_ONLY_SHAPES`` as a JSON array string, empty string, or list.

        Tolerates the empty string, unlike :meth:`parse_cors`, because the variable
        is optional: Compose interpolating an unset ``${READ_ONLY_SHAPES}`` yields
        ``""``, which must mean "no shapes are read-only" rather than a parse error.

        Args:
            v: Raw value from the environment or a direct constructor call.

        Returns:
            A ``list[str]`` of shape URIs.
        """
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            return json.loads(stripped)
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: str | list[str]) -> list[str]:
        """Accept ``CORS_ORIGINS`` as a JSON array string or a plain Python list.

        Docker Compose and ``.env`` files both supply environment variables as
        raw strings.  This validator transparently handles either form:

        - String (from env): ``'["http://localhost:5173"]'`` → parsed via
          :func:`json.loads`.
        - List (from Python tests or programmatic instantiation): passed
          through unchanged.

        Args:
            v: Raw value coming from the environment or a direct constructor
               call.

        Returns:
            A proper Python ``list[str]`` of origin URLs.

        Raises:
            ValueError: If ``v`` is a string that cannot be parsed as JSON.
        """
        if isinstance(v, str):
            return json.loads(v)
        return v
