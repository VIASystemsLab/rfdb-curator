"""Settings for the read service.

There is no subclass here, by design: every field this service reads (store
connection, schema path, CORS, logging, S3, and ``READ_ONLY_SHAPES``) is defined
in ``rfdb_core.config.BaseServiceSettings``. What curator-backend adds on top of
that base is *exactly* the write-side surface (``VOCAB_PATH``, ``DATA_PATH``,
``RESET_DATA_ON_STARTUP``, ``SEED_*``, ``READ_ONLY``, ``MAX_UPLOAD_MB``), and a
reader has no use for any of it.

``READ_ONLY_SHAPES`` is in the shared base rather than that list on purpose: it
reads like a write concern, but it states which shapes are *editable*, which this
service needs in order to serve the same shape catalogue the curator does. That
lets the editor take its shape list from the reader and keep working while the
writer is down.

``extra="ignore"`` on the base config means a shared ``.env`` carrying those
writer variables is harmless here — this service simply does not see them. The
flip side is worth knowing: setting ``READ_ONLY=true`` has no effect on this
process, because there is nothing to switch off.
"""

from rfdb_core.config import BaseServiceSettings

settings = BaseServiceSettings()
