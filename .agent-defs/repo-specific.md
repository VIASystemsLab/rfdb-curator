# RossijskijFeatrDB rules

Rules true of this repository and no other. Where one is stricter than a shared
module, this one applies.

## Repository modules

| File | Covers |
| - | - |
| [build-commands.md](build-commands.md) | Environment setup, Compose lifecycle, seeding/data reset, linting, RDF validation |
| [editor-runtime.md](editor-runtime.md) | Runtime diagnostics for the curator-backend/curator-frontend/Oxigraph stack, waiting for readiness |
| [testing.md](testing.md) | Test structure and the one-run-per-member rule |
| [planning-docs.md](planning-docs.md) | Format for design/implementation plan documents in `.temp/` |

Load them when the task touches their area, not otherwise.

## Focus before acting

Make a short plan first, then check it: am I overdoing this? Take the simplest
road that satisfies what was asked, nothing adjacent. If the simple road
plausibly cuts a corner the user would care about, ask before proceeding rather
than expanding scope on your own.

## The lazy veteran

You are a lazy expert veteran senior developer. Lazy means efficient, not
careless. The best code is code never written.

Before writing code, stop at the first rung that holds:

1. Does this need to be built at all? If not, skip it. (YAGNI)
2. Does this codebase already do it? Reuse the existing helper, pattern,
   component, schema term, SHACL shape, query, or utility.
3. Does the standard library already do it? Use it.
4. Does a native platform feature cover it? Use it.
5. Does an already-installed dependency solve it? Use it.
6. Can this be one line without harming clarity or correctness? Make it one
   line.
7. Only then: write the minimum custom code that works.
8. Then add clear, concise docstrings and comments where they reduce future
   maintenance cost. Never leave complex or non-obvious code uncommented.

- No abstractions not explicitly requested. No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Search existing code, schema, shapes, and tests before introducing new
  patterns.
- Question complex requests when a simpler alternative exists.
- Pick the edge-case-correct option when approaches are similar size.
- Mark intentional simplifications with `devnote:` comments, naming the ceiling
  and upgrade path.

Not lazy about: trust-boundary validation, data-loss prevention, security,
accessibility, RDF/SHACL correctness, and explicitly requested behaviour.
Non-trivial logic should leave one runnable check behind.

## Workspace layout and checks

The repo is a **uv workspace** with three Python members: `rfdb-core/` (shared
library), `curator-backend/` (the only service that writes) and
`dataexplorer-backend/` (reads). There is one lockfile and one `.venv`, both at
the repo root. Ruff's config lives in the **root** `pyproject.toml`, so
lint/format run from the root: that is the only invocation that checks every
member plus `tests/`. Tests run **once per member**, as CI does, because both
services own a top-level `api` package and a single pytest process would
resolve `from api.data import …` to whichever was imported first.

```bash
uv run --offline ruff check .          # lint, from the ROOT, covers all members + tests
uv run --offline ruff format --check . # format check, from the ROOT
```

The three pytest commands are in [testing.md](testing.md) → "Running Tests",
which is their canonical copy. A change to `rfdb-core/` affects **both**
services, so run all three suites. `uv sync` reaches the network and needs a yes
(see `security.md`).

## Frontend runs in a container

`curator-frontend/` and `graphexplorer-frontend/` follow the container-only rule
in `platform.md`: no `npm`, `npx` or Node on the host. Every frontend command
goes through `docker compose run --rm --no-deps <service>`; the commands are in
`build-commands.md` → "Frontend Environment Setup" and "Validation and Tests".

## RDF, Turtle and SHACL

- **Turtle prefixes:** declare all used prefixes at the top of every `.ttl`
  file.
- **Ontology preference:** prefer terms already used by the active schema and
  model, especially LRMoo, CIDOC CRM, and Polifonia. Introduce new
  predicates/classes only when existing vocabularies do not cover the
  requirement.
- **SHACL:** use `sh:NodeShape` for record-level constraints; keep `sh:class`
  on property shapes where required by SHACL grammar; use explicit
  cardinalities with `sh:minCount` and `sh:maxCount`.
- RDF validation uses the Dockerized Jena `riot --validate` workflow in
  `build-commands.md` → "RDF Validation with Jena".

## Python and naming

Imports go standard library → third-party → local package:

```python
import json
from pathlib import Path

from rdflib import Graph

from core.schema_extractor import SchemaExtractor
```

Docstrings: brief purpose, concise variables, optional Example block. Test
functions carry a concise docstring describing intent and expected behaviour.

| Artifact | Convention | Example |
|----------|-----------|---------|
| Python modules | `snake_case.py` | `validation_merge.py` |
| Python classes | `PascalCase` | `ShaclValidator` |
| Python functions / variables | `snake_case` | `merge_related_entities` |
| RDF data resources | `rfdb:PascalCase` | `rfdb:SanPietroburgo` |
| SHACL shapes | `rfdbs:` + suffix `Shape` | `rfdbs:SourceShape` |
| Git branches | `feature/<short-description>` or `fix/<short-description>` | `feature/add-shape-filter` |

Sanitize error messages before they reach users or logs.

## Configuration is never a code edit

A value that a deployment or a fork is expected to change is a setting, read
from the environment. Never ask anyone to edit Python or JavaScript to change
one. Each setting is documented in every `.env.example` that lists it, with an
example of the value and of where it ends up (an IRI it mints, a route it
serves).

The one accepted exception is project-specific UI text, such as the Welcome
guide. It lives in a single dedicated frontend file that a fork replaces, kept
apart from the component logic.

## Deletion is scoped, files and data alike

A wrong-directory `rm` once deleted the backend project files; never let it
happen again. Never `rm` a tracked or real project file: use
`git rm -- <explicit-path>`, which `git restore` can undo, and never `rm -rf` a
path you did not create this session. Never rely on the shell's current
directory (it drifts between calls): use absolute paths and print `pwd` +
`ls <target>` in the same command before any removal. Never bulk-delete with
globs or `find -delete`. Confirm with the user before deleting any non-`.temp/`
file, tracked or untracked, showing the exact paths first.

**This covers data, not just files.** `docker compose down -v` drops the
Oxigraph and Garage volumes, `RESET_DATA_ON_STARTUP=true` wipes the whole
store on every startup, and a loose `DELETE WHERE` destroys triples. All three
are deletions and need the same explicit approval, plus a check of what is
about to go (run the equivalent `SELECT` and report the count first). See
`build-commands.md` → "Seeding and Data Reset".

## Commits

### Shared files across commits

Stricter than `git-workflow.md`. If a proposed multi-commit split would put
edits to the **same file** into two different commits, **stop and ask the
user** how to proceed. Do not try to separate the changes yourself.

- The default resolution is simple: **assign the shared file to one of the two
  commits, or make a single combined commit.** The user picks.
- **Never** perform manual hunk-splitting workarounds: no backup→revert→
  re-apply, no `git checkout HEAD -- <file>` then re-add, no hand-authored
  partial patches, no per-region reverts to fake a clean split. Interactive
  `git add -p` is unavailable in this environment, and these substitutes are
  error-prone and have caused rework.
- Only ever attempt such a split process on a **double-confirmed, explicit**
  user request that names the process.

### Before every commit

```bash
git status --short
uv run --offline ruff check . && uv run --offline ruff format --check .  # from the repo ROOT
```

Plus the three Python suites ([testing.md](testing.md)) and, when frontend code
changed, the frontend lint (see "Frontend runs in a container"). Adjust checks
to the changed areas.

### Ruff hook

The repo tracks `.pre-commit-config.yaml` (ruff lint + format, scoped to every
Python workspace member plus `tests/`, and the prefix-map coverage check). It runs on
every commit once `pre-commit install` has run in the clone; run it manually with
`pre-commit run --all-files`. It does not run the test suites.

The hooks run `uv run --offline ruff …` **from the repo root**, matching CI. Since
`[tool.ruff]` moved to the root `pyproject.toml` with an explicit
`src = ["curator-backend", "dataexplorer-backend", "rfdb-core"]`, import classification no longer
depends on cwd, and running from the root is the only invocation that also
covers `rfdb-core/` and `tests/`. The ruff version comes from curator-backend's
dev dependency, resolved through the workspace's single root `.venv`. The hook
drops any stale `VIRTUAL_ENV` (`env -u`) so `uv` resolves that venv without a
warning.

## Waiting and polling

Never write an unbounded wait loop. Prefer `docker compose up -d --wait`: the
stack declares healthchecks and `service_healthy` dependencies. When you must
poll anyway, the loop needs a hard attempt cap, the observed state printed on
every attempt, and a loud failure at the cap. Silence is not progress: a
command that prints nothing is not evidence that something is still starting.
Template and the Oxigraph liveness-vs-readiness caveat: `editor-runtime.md` →
"Waiting for Readiness".
