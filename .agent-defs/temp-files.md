# The `.temp/` directory

`.temp/` is local scratch space, excluded from git per clone in `.git/info/exclude`
(see "Local-only files" in `AGENTS.md`): design brainstorms, deployment notes,
working documents, handoff notes. It may also hold large local artifacts, so
list it before acting on it. Do not assume everything there is text.

- **Read freely.** It is useful context for what the user is thinking about.
- **Writing is allowed, but say which file and why first.** Where the
  permission rules prompt for the write, the user's answer is their say on that
  file; it does not replace saying why.
- **Overwriting or deleting an existing file needs its own explicit yes**, named
  file and all. These notes are not in git, so a lost one is lost for good.
  Never assume permission to write a *new* file extends to replacing an
  existing one.
- **Never commit anything from `.temp/`.** Never `git add -f` a path under it.
- **Two prefixes, so ownership is visible at a glance:**
  `temp-YYYYMMDD-<topic>.md` for the user's notes, `bot-YYYYMMDD-<topic>.md`
  for yours. A labelling convention, not a permission boundary, on the same
  reasoning as the `[BOT]` commit marker. Be correspondingly more careful with
  `temp-*` files.
- **Point-in-time findings go here, not into the README or the overview.**
  Suspected bugs, inconsistencies, things to verify: they go stale the moment
  someone fixes them, and stale documentation is worse than none. The overview
  describes how the repository *is*; `.temp/` holds what you noticed on a given
  day.
- Content in `.temp/` is **not authoritative**. A design sketch there may be
  stale, rejected, or aspirational. Do not implement from it without confirming
  first, and do not promote it into committed documentation unasked.

## Never point a committed file at local-only content

Not as a markdown link, not in a code comment, not in a CI config, not as
prose, and not as a command or a step that needs the file to exist. This is
stricter than "do not commit the file itself", and it applies to every
committed artifact: source, docs, YAML, Dockerfiles, workflows.

The file does not exist for anyone but this workspace. A reader who clones the
repository gets a dangling link and a pointer to reasoning they cannot read,
which is worse than no citation, because it implies the justification is
documented somewhere when it is not. It also leaks the shape of local scratch
work into a shared artifact.

When a committed file needs the *reasoning* from a scratch note, inline the
reasoning in enough detail to stand on its own and cite nothing.

The same goes for everything else git ignores here, such as
`CLAUDE.md` and `.claude/`. What is forbidden is pointing at a particular
local file, or depending on one. Naming the local-only locations to say how
the repository is laid out, as these rules do, is not.

An absolute path on this machine is local-only too: where the repository is
cloned, anything under the home directory, the session scratchpad. It names one
account on one computer, so it dangles for everyone else and publishes the
account name besides. Write paths relative to the repository root, and say
"this repository" rather than naming where it lives. `scripts/hooks/no-local-paths`
rejects a commit that adds one, but the rule is not to write it at all.

Corollary: a `paths-ignore:` entry for an ignored or excluded path in a CI trigger
is dead config as well as a mention. Such a file can never appear in a
pushed diff, so the entry can never match.
