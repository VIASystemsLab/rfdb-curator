# Python package conventions

`code-style.md` holds the general conventions, including type hints, private
helpers, configuration and dependencies. In Python:

- Type hints use `X | None` unions, not `Optional[X]`.
- `logging.getLogger(__name__)` per module. No `print()` in package code; CLI
  progress output is the exception.
- Configuration is read from the environment through one settings layer, never
  scattered `os.environ` calls: a typed class that validates at startup
  (pydantic-settings where pydantic is already a dependency, otherwise a frozen
  dataclass filled in one module). Prefer code that receives the settings
  object, so tests can construct it with overrides.
- Module docstrings explain why the module exists.
- Blocking CPU work called from an `async def` route goes through a thread pool,
  never inline.
- The package manager is uv add / uv lock: `pyproject.toml` and the lockfile are
  edited through it.

## Checks

Run the checks the repository defines, as CI runs them. Where this repository's
own rules name its check commands, those win. For a single package:

```bash
uv run --offline ruff check .
uv run --offline pytest -q
```

`--offline` stops a moved lockfile from turning a test run into a download. If
it fails for a missing package, ask before syncing.

In a uv workspace (a `[tool.uv.workspace]` table in the root `pyproject.toml`)
whose members share top-level package names, run pytest once per member, from
each member's directory: a single run from the root collects every member under
one rootdir, and the same-named packages shadow each other.

Run them, and still reason about correctness explicitly rather than treating
green as proof. Say plainly when a change is unverified.

## Version bumps

A bump here touches `pyproject.toml`, any deployment manifest and the lockfile,
which is why `code-style.md` asks for the exact number before any of them.
