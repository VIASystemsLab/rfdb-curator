"""
Curator-backend settings loaded exclusively from environment variables or a ``.env`` file.

Extends :class:`rfdb_core.config.BaseServiceSettings` — which carries everything
more than one service needs (triplestore URL, schema path, CORS, logging, S3
credentials) — with the fields that are the **writer's alone**: what to seed,
whether to reset the store, the read-only guards, and the upload ceiling. None of
these mean anything in a read-only service, which is why they live here.

No default values are hardcoded for the required fields.  Every setting MUST be
supplied through:

  - A ``.env`` file in the working directory, ``curator-backend/`` (copy
    ``.env.example`` to ``.env``).
  - Environment variables injected by Docker Compose or the host shell.

This ensures a single source of truth per deployment: the ``.env`` file for local
development, the ``environment:`` block in ``docker-compose.yml`` for Docker.

See ``docs/deployment.md`` for a full reference of every variable.
"""

from __future__ import annotations

import json

from pydantic import Field, field_validator

from rfdb_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Curator-backend configuration; instantiated once as a module-level singleton.

    Inherited fields (see :class:`rfdb_core.config.BaseServiceSettings`):
    ``triplestore``, ``oxigraph_url``, ``data_graph_uri``, ``oxigraph_load_timeout``,
    ``schema_path``, ``read_only_shapes``, ``cors_origins``, the ``log_*`` group,
    and the ``s3_*`` group.

    Attributes:
        vocab_paths: Paths to the controlled-vocabulary Turtle files loaded into the store on
            every startup when ``seed_vocab_on_startup`` is ``true``.
            Example: ``["data/rfdb_vocab.ttl"]``

        data_path: Path to the sample-data Turtle file — test fixture data, seeded only when
            ``seed_test_data_on_startup`` is ``true``.
            Example: ``data/rfdb_data.ttl``

        reset_data_on_startup: Wipe the store and re-seed at startup. When
            ``true``, every graph is erased (``DROP ALL`` plus ``CLEAR DEFAULT``)
            before any seed files are loaded, not only ``data_graph_uri``.
            Should be ``false`` in production to avoid accidental data loss.

        seed_vocab_on_startup: Load ``vocab_paths`` into the store at startup.
            Should be ``true`` in all environments so that controlled vocabulary
            terms are always present.

        seed_test_data_on_startup: Load ``data_path`` at startup.  Set to
            ``true`` only in development/test environments; must be ``false``
            in production.

        read_only: Disable all mutating API operations when ``true``.
            Write and delete routes return HTTP 403 with a clear message.
            Useful for demo or presentation mode.
    """

    # ------------------------------------------------------------------ #
    # Seed file paths (relative to the service working directory)          #
    # ------------------------------------------------------------------ #

    vocab_paths: list[str] = Field(validation_alias="VOCAB_PATH")
    """Paths to vocabulary Turtle files seeded on startup.

    Supplied as a JSON array string via the ``VOCAB_PATH`` environment variable,
    e.g. ``'["data/rfdb_vocab.ttl","data/glottolog_language.ttl"]'``.
    The ``parse_vocab_paths`` validator parses the JSON array string; a list value
    passed directly (in tests) is returned unchanged.
    """

    data_path: str
    """Path to the test-fixture Turtle file (e.g. ``data/rfdb_data.ttl``)."""

    # ------------------------------------------------------------------ #
    # Startup behaviour — writer-only                                      #
    # ------------------------------------------------------------------ #

    reset_data_on_startup: bool
    """When ``true``, wipes every graph in the store before seeding.  Keep
    ``false`` in production to prevent accidental data loss."""

    seed_vocab_on_startup: bool
    """When ``true``, loads ``vocab_paths`` into the store at every startup.
    Recommended ``true`` in all environments."""

    seed_test_data_on_startup: bool
    """When ``true``, loads ``data_path`` at startup.  Should only be
    ``true`` in development or test environments."""

    # ------------------------------------------------------------------ #
    # Write guards                                                         #
    # ------------------------------------------------------------------ #

    read_only: bool = False
    """When ``true``, disable write/delete API routes and allow only reads.
    Intended for demo/presentation environments.

    Distinct from *deployment mode*: this makes a deployed curator refuse writes,
    whereas mode decides whether the curator is deployed at all."""

    # ``read_only_shapes`` is inherited: besides gating writes, it tells any
    # client which shapes are editable, so the reader needs it too: both services
    # serve the same shape list, and the editor works with the writer down.

    max_upload_mb: int = 500
    """Per-file upload ceiling in megabytes. Uploads exceeding this are
    rejected with HTTP 413 (checked while streaming, so an oversize body is
    never fully buffered). Set to ``0`` to disable the cap entirely."""

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("vocab_paths", mode="before")
    @classmethod
    def parse_vocab_paths(cls, v: str | list[str]) -> list[str]:
        """Accept ``VOCAB_PATH`` as a JSON array string or a plain Python list.

        Args:
            v: Raw value from the environment or a direct constructor call.

        Returns:
            A ``list[str]`` of file paths.

        Raises:
            ValueError: If ``v`` is a string that cannot be parsed as JSON.
        """
        if isinstance(v, str):
            return json.loads(v)
        return v


# --------------------------------------------------------------------------- #
# Module-level singleton                                                        #
# --------------------------------------------------------------------------- #

settings = Settings()
"""Module-level singleton consumed by the rest of the application.

Import and use as::

    from core.config import settings

    print(settings.oxigraph_url)
"""
