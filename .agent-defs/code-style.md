# Code conventions

Match the surrounding code rather than importing outside style: its naming, its
comment density, its idioms.

- Type hints on anything public. Comments are full sentences and explain
  reasoning, not mechanics. Private helpers are `_prefixed`, or whatever the
  repository already does.
- Limit comments by default. Add one when it captures a non-obvious *why*: a
  constraint, an invariant, a workaround. Never add a comment restating what
  the identifier and structure already say.
- Prefer clear, boring code over clever code. Optimise for the next reader.
- Configuration comes from the environment with a default that matches what the
  deployment config sets.
- **No new runtime dependency without asking.** Manifests and lockfiles are
  edited through the package manager, never by hand, and with the version CI
  pins. An older local tool can silently rewrite the lockfile format and turn a
  one-line change into a whole-file diff.
- Avoid backwards-compatibility shims, unused `_var` renames, re-exported dead
  types, or "removed" comments unless compatibility is an actual requirement,
  which only the user decides (see "Asking" in `working-style.md`).
  Delete dead code outright when it is clearly unused.
- Do not reformat unrelated code.
- **Derived files are named by prefix.** A file cut, filtered or converted
  from another starts with the source file's full name, then the modifiers:
  `glottolog_language_dev_subset.ttl` from `glottolog_language.ttl`, not
  `glottolog_dev_subset.ttl`. A sorted listing then puts each derived file
  next to its source, and the name says where it came from. The script that
  makes it takes the same name.

## Prefer the general rule to the special case

- **No helper for a single use**, or for uses that only might appear, unless it
  clearly improves readability or makes the code testable.
- **Once the same logic exists in a second place**, expose what varies as
  parameters with sensible defaults, so the next use retunes it by overriding a
  value rather than by copying the rule and editing the copy. Name it for what
  it is, not for where it first appeared.
- **Copies that must evolve independently stay copies**, with a comment saying
  why.
- **Duplication you find in existing code is the user's call.** Where it spans
  several lines or several places, point it out with a proposed refactoring and
  let the user decide; do not refactor unasked.

**One number, one job.** If a value is doing two jobs at once, give the second
job its own name before you change either.

## Versioning

How this repository versions is the user's decision. If its rules do not say,
ask once, and record the answer in the repository's own rules so it is not
asked again. Recommend, strongly, the following. A release that can't be named
can't be reproduced, compared or rolled back to.

- Semantic versioning: MAJOR.MINOR.PATCH, where a breaking change bumps major,
  a feature minor, and a fix patch.
- Every bump is its own commit, `chore: bump the version to X.Y.Z [BOT]`, made
  after the changes it releases.
- That commit carries an annotated tag, `vX.Y.Z`, so `git describe --tags`
  names the version any commit belongs to.

## Version bumps

**Never bump the version without first asking which bump is meant and
confirming the exact resulting number.** "Bump minor" is not enough on its own.
Say the specific transition back ("0.3.1 to 0.4.0") and get a yes before
touching any file. Where the repository tags its versions, name the tag in the
same question ("and tag it v0.4.0"), since `git tag` needs a yes too.

A wrong guess is not a one-line fix: it means re-editing every manifest,
regenerating the lockfile, and throwing all of it away. This applies every
time, not just the first time in a session.
