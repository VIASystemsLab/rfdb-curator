"""The source fingerprint: changes with the code, ignores caches and environments."""

from __future__ import annotations

from pathlib import Path

from rfdb_core.source_hash import source_hash


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_same_files_give_the_same_hash(tmp_path: Path) -> None:
    """Two copies of a tree, wherever they sit, hash the same: the container and the checkout."""
    files = {"app.py": "x = 1\n", "api/data.py": "y = 2\n"}
    assert source_hash(_tree(tmp_path / "a", files)) == source_hash(_tree(tmp_path / "b", files))


def test_an_edit_a_new_file_or_a_rename_changes_it(tmp_path: Path) -> None:
    """Content, file set and paths all count."""
    root = _tree(tmp_path / "svc", {"app.py": "x = 1\n"})
    before = source_hash(root)

    (root / "app.py").write_text("x = 2\n")
    edited = source_hash(root)
    (root / "new.py").write_text("")
    added = source_hash(root)
    (root / "new.py").rename(root / "renamed.py")
    renamed = source_hash(root)

    assert len({before, edited, added, renamed}) == 4


def test_caches_environments_and_other_files_are_ignored(tmp_path: Path) -> None:
    """Bytecode, a virtualenv, hidden folders and non-Python files do not count."""
    root = _tree(tmp_path / "svc", {"app.py": "x = 1\n"})
    before = source_hash(root)
    _tree(
        root,
        {
            "__pycache__/app.py": "cached",
            ".venv/lib/site.py": "installed",
            ".hidden/tool.py": "local",
            "data/vocab.ttl": "<a> <b> <c> .",
        },
    )
    assert source_hash(root) == before


def test_root_order_counts(tmp_path: Path) -> None:
    """Service then library is not library then service."""
    service = _tree(tmp_path / "svc", {"app.py": "x = 1\n"})
    library = _tree(tmp_path / "lib", {"core.py": "y = 2\n"})
    assert source_hash(service, library) != source_hash(library, service)
