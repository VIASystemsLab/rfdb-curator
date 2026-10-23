"""Structured logging configuration for both RossijskijFeatrDB backend services.

Configures two handlers on the root logger:

- ``StreamHandler`` (stderr) — human-readable, for Docker/console.
- ``RotatingFileHandler`` — JSON-lines format, one record per line, for
  offline inspection and log aggregation.

JSON-lines format
-----------------
Every line is a self-contained JSON object::

    {"ts": "2026-06-02T14:00:00.123Z", "level": "INFO", "logger": "api.access",
     "msg": "GET /api/v1/dataexplorer/entities", "status": 200, "ms": 12}

Extra key-value pairs passed via ``logging.info(..., extra={...})`` are merged
into the top-level object so they appear alongside the standard fields without
any special parsing.

Usage
-----
Call ``configure_logging(log_file, log_level)`` once at process startup
(inside the FastAPI lifespan) before any other code runs::

    from rfdb_core.logging_config import configure_logging
    configure_logging(settings.log_file, settings.log_level)
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Merge any extra fields passed via logging.info(..., extra={...})
        _skip = logging.LogRecord.__dict__.keys() | {
            "message",
            "asctime",
            "exc_info",
            "exc_text",
            "stack_info",
        }
        for key, value in record.__dict__.items():
            if key not in _skip and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    log_file: str,
    log_level: str = "INFO",
    truncate_on_startup: bool = False,
    truncate_on_fresh_container_start: bool = False,
    startup_marker_file: str = "/tmp/rfdb_backend_logging_initialized",
) -> None:
    """Configure root logger with console + rotating JSON-lines file handler.

    Args:
        log_file: Path to the output log file (e.g. ``logs/app.jsonl``).
            Parent directories are created automatically.
        log_level: Minimum log level string (``DEBUG``, ``INFO``, ``WARNING``,
            ``ERROR``, ``CRITICAL``).  Case-insensitive.
        truncate_on_startup: When ``True``, open ``log_file`` in write mode
            so previous contents are discarded at process start.
        truncate_on_fresh_container_start: When ``True``, truncate only on the
            first process start inside a freshly created container, then append
            on subsequent restarts of that same container.
        startup_marker_file: Marker file path used to detect whether the
            current container has already completed one startup.
    """
    from pathlib import Path

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)

    marker_exists = os.path.exists(startup_marker_file)
    should_truncate = truncate_on_startup or (
        truncate_on_fresh_container_start and not marker_exists
    )

    # Rotating file handler — JSON-lines, max 10 MB, keep 5 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        mode="w" if should_truncate else "a",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(_JsonFormatter())

    # Console handler — plain text for Docker/terminal readability
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Replace whatever is on root rather than stacking a second pair, which
    # uvicorn --reload would otherwise do on every module re-import.
    #
    # close() on the way out is the part that matters: `root.handlers.clear()`
    # alone would drop the reference to an open RotatingFileHandler without
    # releasing its file descriptor, leaking one handle per reconfiguration.
    # Python ignores ResourceWarning by default, so the leak would go unreported.
    # Closing a StreamHandler is safe: only FileHandler.close() touches the
    # stream, so sys.stderr (and pytest's capture buffers) survive this.
    for existing in root.handlers[:]:
        root.removeHandler(existing)
        existing.close()

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    if truncate_on_fresh_container_start and not marker_exists:
        try:
            os.makedirs(os.path.dirname(startup_marker_file), exist_ok=True)
            with open(startup_marker_file, "w", encoding="utf-8"):
                pass
        except Exception:
            # Marker creation failure should not block app startup.
            pass
