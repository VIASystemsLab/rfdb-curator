"""``configure_logging`` must be safe to call more than once.

It is called more than once in practice: ``uvicorn --reload`` re-imports the
module on every source change, and the test suites reconfigure per imported app.
Each call must neither duplicate handlers nor leak resources. Clearing
``root.handlers`` alone handles the first but not the second: it drops the
reference to an already-open ``RotatingFileHandler`` without closing it, leaking
one file descriptor per reconfiguration.

Python ignores ``ResourceWarning`` by default, so a leak of exactly this shape is
invisible to a green suite. The test below turns the warning on explicitly.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from rfdb_core.logging_config import configure_logging


@pytest.fixture
def isolated_root_logger() -> Iterator[logging.Logger]:
    """Snapshot and restore the root logger around a test.

    ``configure_logging`` replaces every handler on the root logger, pytest's own
    log-capture handlers included. Without this the first test to call it would
    silently disable capturing for the rest of the session.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers.clear()
    try:
        yield root
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


def _file_handlers(root: logging.Logger) -> list[logging.FileHandler]:
    return [h for h in root.handlers if isinstance(h, logging.FileHandler)]


def test_repeated_configuration_leaks_no_file_handle(
    isolated_root_logger: logging.Logger, tmp_path: Path
) -> None:
    """Five calls leave one open log file, not five.

    The assertion is on ``ResourceWarning`` rather than on a descriptor count
    because that is the symptom a future regression would produce: dropping a
    handler without closing it is exactly what CPython reports this way.
    """
    log_file = tmp_path / "app.jsonl"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _ in range(5):
            configure_logging(str(log_file), "INFO")

    leaked = [w for w in caught if w.category is ResourceWarning]
    assert not leaked, f"unclosed handlers left behind: {[str(w.message) for w in leaked]}"

    open_logs = [h for h in _file_handlers(isolated_root_logger) if not h.stream.closed]
    assert len(open_logs) == 1, (
        f"expected exactly one live file handler, found {len(open_logs)} — "
        "handlers are accumulating instead of being replaced."
    )


def test_reconfiguration_keeps_the_logger_working(
    isolated_root_logger: logging.Logger, tmp_path: Path
) -> None:
    """Closing the outgoing handlers must not close the incoming ones.

    The failure mode this guards against is over-eager cleanup: ``close()`` on a
    ``StreamHandler`` deliberately does *not* close its stream, so ``sys.stderr``
    survives. If that ever stopped being true, the file below would still be
    written but every console line would vanish — so both are asserted.
    """
    log_file = tmp_path / "app.jsonl"
    configure_logging(str(log_file), "INFO")
    configure_logging(str(log_file), "INFO")

    logging.getLogger("rfdb.probe").info("after reconfiguration")
    for handler in isolated_root_logger.handlers:
        handler.flush()

    assert "after reconfiguration" in log_file.read_text(encoding="utf-8")

    streams = [
        h
        for h in isolated_root_logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert streams, "the console handler was dropped"
    assert all(not h.stream.closed for h in streams), "closing a discarded handler closed stderr"
