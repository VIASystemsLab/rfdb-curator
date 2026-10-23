"""Unit tests for the startup data seeder.

Covers the ``vocab_paths`` list handling:
- a bare-string VOCAB_PATH (not a JSON array) is refused at startup
- JSON array with one path seeds one file
- JSON array with two paths seeds both files
- a missing file returns loaded=False without raising
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic_settings import SettingsError

BACKEND_DIR = Path(__file__).resolve().parents[2] / "curator-backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oxigraph_stub():
    """Return a minimal Oxigraph stub that records load_turtle calls."""
    stub = MagicMock()
    stub.load_turtle = MagicMock()
    return stub


# ---------------------------------------------------------------------------
# Config validator: parse_vocab_paths
# ---------------------------------------------------------------------------


class TestParseVocabPaths:
    """Tests for Settings.parse_vocab_paths field validator."""

    def setup_method(self):
        # Re-import with a clean environment for each test.
        sys.modules.pop("core.config", None)

    def _make_settings(self, vocab_path_value: str, monkeypatch):
        monkeypatch.setenv("OXIGRAPH_URL", "http://localhost:7878")
        monkeypatch.setenv("DATA_GRAPH_URI", "https://rfdb.it/graph/data")
        monkeypatch.setenv("SCHEMA_PATH", "schema/rfdb_schema.ttl")
        monkeypatch.setenv("VOCAB_PATH", vocab_path_value)
        monkeypatch.setenv("DATA_PATH", "data/rfdb_data.ttl")
        monkeypatch.setenv("RESET_DATA_ON_STARTUP", "false")
        monkeypatch.setenv("SEED_VOCAB_ON_STARTUP", "false")
        monkeypatch.setenv("SEED_TEST_DATA_ON_STARTUP", "false")
        monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')
        sys.modules.pop("core.config", None)
        config_mod = importlib.import_module("core.config")
        # Re-instantiate Settings with the patched env.
        return config_mod.Settings()

    def test_bare_string_is_refused(self, monkeypatch):
        """VOCAB_PATH must be a JSON array, even for one file: a bare path fails at startup."""
        with pytest.raises(SettingsError, match="vocab_paths"):
            self._make_settings("data/rfdb_vocab.ttl", monkeypatch)

    def test_json_array_single_path(self, monkeypatch):
        """A JSON array with one path is parsed correctly."""
        settings = self._make_settings('["data/rfdb_vocab.ttl"]', monkeypatch)
        assert settings.vocab_paths == ["data/rfdb_vocab.ttl"]

    def test_json_array_two_paths(self, monkeypatch):
        """A JSON array with two paths is parsed correctly."""
        settings = self._make_settings(
            '["data/rfdb_vocab.ttl", "data/glottolog_language.ttl"]', monkeypatch
        )
        assert settings.vocab_paths == ["data/rfdb_vocab.ttl", "data/glottolog_language.ttl"]


# ---------------------------------------------------------------------------
# seed_store: iterates all vocab_paths
# ---------------------------------------------------------------------------


class TestSeedStore:
    """Tests for seed_store() with vocab_paths list."""

    def setup_method(self):
        sys.modules.pop("core.seeder", None)

    def _import_seeder(self):
        return importlib.import_module("core.seeder")

    def test_missing_file_returns_loaded_false(self, tmp_path):
        """A path that does not exist produces loaded=False without raising."""
        seeder = self._import_seeder()
        ox = _make_oxigraph_stub()
        missing = str(tmp_path / "nonexistent.ttl")

        report = seeder.seed_store(
            store=ox,
            vocab_paths=[missing],
            test_data_path=str(tmp_path / "data.ttl"),
            seed_vocab=True,
            seed_test_data=False,
        )

        assert report["results"][0]["loaded"] is False
        assert report["results"][0]["reason"] == "missing"
        ox.load_turtle.assert_not_called()

    def test_single_vocab_path_is_loaded(self, tmp_path):
        """A single path in vocab_paths is loaded once."""
        vocab = tmp_path / "vocab.ttl"
        vocab.write_text("@prefix ex: <http://example.org/> .\n", encoding="utf-8")

        seeder = self._import_seeder()
        ox = _make_oxigraph_stub()

        report = seeder.seed_store(
            store=ox,
            vocab_paths=[str(vocab)],
            test_data_path=str(tmp_path / "data.ttl"),
            seed_vocab=True,
            seed_test_data=False,
        )

        assert len(report["results"]) == 1
        assert report["results"][0]["loaded"] is True
        ox.load_turtle.assert_called_once()

    def test_two_vocab_paths_both_loaded(self, tmp_path):
        """Two paths in vocab_paths are each loaded once."""
        v1 = tmp_path / "vocab.ttl"
        v2 = tmp_path / "glottolog.ttl"
        v1.write_text("@prefix ex: <http://example.org/> .\n", encoding="utf-8")
        v2.write_text("@prefix gl: <http://glottolog.org/> .\n", encoding="utf-8")

        seeder = self._import_seeder()
        ox = _make_oxigraph_stub()

        report = seeder.seed_store(
            store=ox,
            vocab_paths=[str(v1), str(v2)],
            test_data_path=str(tmp_path / "data.ttl"),
            seed_vocab=True,
            seed_test_data=False,
        )

        assert len(report["results"]) == 2
        assert all(r["loaded"] is True for r in report["results"])
        assert ox.load_turtle.call_count == 2

    def test_seed_vocab_false_skips_all_vocab_paths(self, tmp_path):
        """When seed_vocab=False, no vocab files are loaded regardless of the list."""
        vocab = tmp_path / "vocab.ttl"
        vocab.write_text("@prefix ex: <http://example.org/> .\n", encoding="utf-8")

        seeder = self._import_seeder()
        ox = _make_oxigraph_stub()

        report = seeder.seed_store(
            store=ox,
            vocab_paths=[str(vocab)],
            test_data_path=str(tmp_path / "data.ttl"),
            seed_vocab=False,
            seed_test_data=False,
        )

        assert report["results"] == []
        ox.load_turtle.assert_not_called()


# ---------------------------------------------------------------------------
# bootstrap_store: the step both the lifespan and scripts/seed.py call
# ---------------------------------------------------------------------------


class _BootstrapStoreStub:
    """A store that records the calls bootstrap_store is expected to make.

    ``health_after`` models the cold-start race: the first N polls report the
    store as unreachable, matching a container whose HTTP port is not listening
    yet even though Docker's liveness check already passed.
    """

    def __init__(self, *, health_after: int = 0):
        self.calls: list[str] = []
        self.health_calls = 0
        self._health_after = health_after
        self.loaded: list[str] = []

    def health(self) -> bool:
        self.health_calls += 1
        return self.health_calls > self._health_after

    def clear_store(self) -> None:
        self.calls.append("clear_store")

    def load_turtle(self, ttl: str) -> None:
        self.calls.append("load_turtle")
        self.loaded.append(ttl)


def _bootstrap_settings(tmp_path, **overrides):
    """The subset of Settings that bootstrap_store reads, as a plain namespace."""
    vocab = tmp_path / "vocab.ttl"
    vocab.write_text("@prefix ex: <http://example.org/> .\n", encoding="utf-8")
    defaults = {
        "oxigraph_url": "http://localhost:7878",
        "reset_data_on_startup": False,
        "vocab_paths": [str(vocab)],
        "data_path": str(tmp_path / "data.ttl"),
        "seed_vocab_on_startup": True,
        "seed_test_data_on_startup": False,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class TestBootstrapStore:
    """The seed entrypoint has to work with no FastAPI app in the picture.

    ``scripts/seed.py`` runs it as a one-shot job (read-only deploy mode has no
    writer process to seed on startup), so nothing here may depend on
    ``app.state`` or a lifespan.
    """

    def _import_seeder(self):
        return importlib.import_module("core.seeder")

    def test_callable_outside_an_app(self, tmp_path):
        """Poll, skip the reset, seed — driven only by a store and a settings object."""
        seeder = self._import_seeder()
        store = _BootstrapStoreStub()

        report = seeder.bootstrap_store(store, _bootstrap_settings(tmp_path))

        assert store.health_calls == 1  # readiness poll ran
        assert store.calls == ["load_turtle"]  # no clear_store when reset is off
        assert report["seedVocab"] is True
        assert [r["loaded"] for r in report["results"]] == [True]

    def test_idempotent(self, tmp_path):
        """Two runs load the same triples and report identically.

        Re-seeding is safe because the store merges triples; the job is expected
        to be re-runnable after adding a vocabulary file.
        """
        seeder = self._import_seeder()
        settings = _bootstrap_settings(tmp_path)

        first_store = _BootstrapStoreStub()
        first = seeder.bootstrap_store(first_store, settings)
        second_store = _BootstrapStoreStub()
        second = seeder.bootstrap_store(second_store, settings)

        assert first == second
        assert first_store.loaded == second_store.loaded

    def test_waits_for_a_store_that_is_not_ready_yet(self, tmp_path, monkeypatch):
        """The readiness poll retries, and nothing is written before it clears."""
        seeder = self._import_seeder()
        monkeypatch.setattr(seeder.time, "sleep", lambda _s: None)
        store = _BootstrapStoreStub(health_after=3)

        seeder.bootstrap_store(store, _bootstrap_settings(tmp_path))

        assert store.health_calls == 4
        assert store.calls == ["load_turtle"]

    def test_reset_wipes_before_seeding(self, tmp_path):
        """reset_data_on_startup=true clears first, then seeds — never the reverse."""
        seeder = self._import_seeder()
        store = _BootstrapStoreStub()

        seeder.bootstrap_store(store, _bootstrap_settings(tmp_path, reset_data_on_startup=True))

        assert store.calls == ["clear_store", "load_turtle"]

    def test_unreachable_store_aborts(self, tmp_path, monkeypatch):
        """A store that never answers raises rather than seeding into the void."""
        seeder = self._import_seeder()
        monkeypatch.setattr(seeder, "_READY_TIMEOUT_S", 0.0)
        monkeypatch.setattr(seeder.time, "sleep", lambda _s: None)
        store = _BootstrapStoreStub(health_after=10**6)

        with pytest.raises(RuntimeError, match="not reachable"):
            seeder.bootstrap_store(store, _bootstrap_settings(tmp_path))

        assert store.calls == []  # nothing written, nothing cleared
