# Testing

## Running Tests

One pytest run per Python workspace member: three commands, three interpreters. **This is the
canonical copy**; [repo-specific.md](repo-specific.md) links here rather than repeating it.

```bash
# `uv run` resolves the workspace venv at the REPO ROOT from any member directory;
# no manual `source .venv/bin/activate` needed.
cd rfdb-core               && uv run --offline python -m pytest -c pyproject.toml ../tests/core -v
cd ../curator-backend      && uv run --offline python -m pytest -c pyproject.toml ../tests/curator -v --ignore=../tests/curator/test_backend_api_data.py
cd ../dataexplorer-backend && uv run --offline python -m pytest -c pyproject.toml ../tests/dataexplorer -v
```

The curator run leaves out the live suite, `tests/curator/test_backend_api_data.py`:
it writes into whatever stack is running, so it runs only as described in "Running
the live suite".

`-c pyproject.toml` is needed because pytest cannot auto-discover a member's config
from `../tests/`: the member directory is not an ancestor of the test files. Each
member's `testpaths` also names its own subdirectory, so a bare `pytest` inside a
member picks up the right suite.

**Why three runs and not one.** curator-backend and dataexplorer-backend both own a
top-level `api` package. In a single process `from api.data import …` resolves to
whichever service was imported first and `sys.modules` caches it, so half the suite
would silently test the wrong service. Separate processes make that impossible
rather than merely unlikely.
CI mirrors this with one job per member.

## Test Structure

One subdirectory per component, each collected by that component's pytest config:

```text
tests/
├── conftest.py            # shared env: the BaseServiceSettings variables only
├── core/                  # rfdb-core: schema constraints, prefixes, store seam
│   ├── test_schema_constraints.py
│   ├── test_class_targeted_validation.py
│   ├── test_triplestore_contract.py
│   └── ...
├── curator/               # curator-backend: writes, validation, seeding, staging
│   ├── conftest.py        # writer-only env: VOCAB_PATH, SEED_*, RESET_*
│   ├── test_backend_api_data.py       # live HTTP; spans BOTH services (see below)
│   ├── test_backend_app_startup.py
│   ├── test_digital_copies.py         # staging, reconciliation, cleanup
│   └── ...
└── dataexplorer/          # dataexplorer-backend: reads, graph, meta
    ├── test_app_contract.py           # structural: no writes, 3 app.state entries
    ├── test_api_data_read.py          # list/counts/get, incl. empty-store tolerance
    ├── test_digital_copies_read.py    # download + /meta/files
    ├── test_api_graph.py
    ├── test_api_meta.py
    └── ...
```

A test belongs to the component whose code it imports. Where a subject spans both
(the SHACL semantics the write path relies on, say), it goes to `tests/core/` if it
imports no service module.

`tests/curator/test_backend_api_data.py` is the exception worth knowing about: it
POSTs to curator-backend and reads back from dataexplorer-backend, so it needs both
services up. Override `RFDB_API_BASE_URL` / `RFDB_READ_API_BASE_URL` to point it at
another stack.

### Running the live suite

The dev backends run `uvicorn --reload` over the source tree, so any edit, checkout
or branch switch restarts them, and a run during a restart tests half the old code
and half the new.

1. **Ask first.** The suite writes into the running stack's stores. Before every
   run, ask whether to run it now, or whether the user wants to preserve the
   volumes first (back them up, or point the suite at another stack).
2. **Settle the tree first.** Finish every switch, checkout or edit, and never
   start a run in the same command as one.
3. **Let the fixture judge the stack.** `api_client` skips when nothing answers
   `/health`, and fails, listing every answer, when the stack answers but is not
   healthy three times in a row within 30 one-second tries. It then fails when a
   backend's `source` hash differs from this checkout's (another worktree, an
   unfinished reload, an old image) or is missing (`REPORT_SOURCE_HASH` off), and
   skips when the stack does not serve the rfdb shapes the payloads follow. A skip
   is not a pass: report it as a skip.
4. **Check the checkout did not move.** Compare `git rev-parse HEAD` and
   `git status --short` before and after; if either changed, the run is void.
5. **Report every run.** A failed run followed by a passing one is reported as
   both, with the cause.

The suite writes records under fresh IRIs into the stack's data graph, and uploads
PDFs into Garage, and deletes neither. Run it against a dev stack only. Afterwards,
offer to wipe both stores and ask whether to wipe now; never wipe without that yes.
The wipe procedure: [build-commands.md](build-commands.md) → "Wiping the dev stores".

## Frontend changes: no browser (Mandatory)

Never open, launch, or drive a browser (Playwright, Puppeteer, headless Chrome, or similar) to verify frontend changes. Do not install or invoke browser-automation tooling for this purpose.

Instead: verify what is verifiable without a browser (API responses via `curl`, backend tests, lint/build), then tell the user the change is ready for them to test manually in their own browser and report back what they see.

### Previewing an inline SVG (e.g. the WelcomeGuide WEMI diagram)

To eyeball an SVG that lives inside a React component (no browser needed), render
it to PNG with macOS Quick Look and Read the PNG:

```bash
qlmanage -t -s 1000 -o <scratchpad-dir> diagram.svg   # writes diagram.svg.png
```

Two gotchas, both one-time:

- **Quick Look emits a square `s×s` thumbnail.** Portrait content fit to width
  overflows and the bottom is cropped. Fix: pad the standalone SVG's `viewBox`
  (and `width`/`height`) to roughly square/landscape so nothing is cut. This is
  preview-only; leave the real component's viewBox alone.
- **Component SVGs use CSS classes from a stylesheet**, so a standalone copy
  renders unstyled (lines have no stroke → invisible). Inline a `<style>` block
  with concrete values (copy from the component's `.css`), and add a dark
  background `<rect>` when the UI is dark-themed. Then paste the component's
  `<g>/<line>/<text>` markup verbatim.

No install needed (`qlmanage` ships with macOS). Do NOT rasterize via a headless
browser, by the same rule as above.

## Test Docstrings

Test functions should include concise docstrings describing the intent and expected behavior.

## Before Committing

The checklist is in [repo-specific.md](repo-specific.md) → "Before every commit".

## Hooks

What the pre-commit hooks run: [build-commands.md](build-commands.md) → "Pre-commit
hooks". They do not run the test suites.
