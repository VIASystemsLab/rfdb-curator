# Git workflow

## Permission

Requires explicit permission, every time: `git commit`, `git push`, `git tag`,
`git reset --hard`, `git clean -f`, `git stash drop`, `git rebase`,
`git cherry-pick`, `git commit --amend` on anything pushed, anything that
rewrites history, opening or updating a PR, switching branch (`git switch`,
`git checkout <branch>`), creating one (`git switch -c`, `git branch <name>`).
Permission to commit or push does not waive the checks: see "Verifying" in
`working-style.md`.

Free without asking, all read-only: `git status`, `git diff`, `git log`,
`git show`, `git blame`, `git remote -v`, and `git rev-parse` (the current
branch is `git rev-parse --abbrev-ref HEAD`). Every other `git branch` form
asks, listing included, because the permission rules cannot tell a listing
from a deletion.

`git checkout -- <path>` discards a file's changes. That is a destructive file
operation, not a branch switch, and it needs permission too.

## Staging

Never `git add -A`, `git add .`, `git add --all`, `git add -u`, `git commit -a`
or `git commit -am`. Always enumerate:

```bash
git add src/components/Nav.tsx src/styles/nav.css
```

A repository has large ignored trees sitting next to the source: build output,
dependency directories, data dumps, `.env` files. A bulk add is one
`.gitignore` gap away from committing a build artifact or a secret. Enumerating
paths makes that impossible.

Run `git status --short` before staging and read it. Run
`git diff --cached --stat` after, and confirm the list is exactly what you
meant.

Never split one file across two commits, and never reconstruct, stash-juggle or
partially undo finished work to produce a tidier history. Stage whole files. If
that means a commit covers slightly more than its topic, say so in the message
rather than surgically dividing the file.

## One commit per topic

Three unrelated changes in the tree means three commits with three messages,
not one commit called "update".

- Do not mix a source change with an unrelated content or docs change.
- Do not mix a refactor with a behaviour change.
- Do not mix a config or `.gitignore` change with feature work.
- Do not fold in "while I was there" edits. Commit them separately, or leave
  them unstaged and mention them.

State the intended commit split to the user **before** asking for permission,
so they approve a plan and not a surprise.

## One commit per command

Run each `git commit` in its own tool call. Never chain two commits in one
command, and never chain a commit with the `git add` for the next topic. A
chain keeps going past a commit that failed, and a failed commit leaves its
files staged, so the next commit in the chain sweeps them up under the wrong
message. Undoing that means rewriting history.

Immediately before each commit, run `git diff --cached --name-only` and compare
it with the paths meant for that commit. If anything else is staged, stop.

When a commit fails, for any reason (a hook rejection, a signing error, anything
else), stop. Stage and commit nothing more until the failure is understood and
the index holds exactly what that commit was meant to hold.

Pass a multi-line message with `-F <file>`, the file in the session scratchpad
directory, or with a heredoc that ends the command:

```bash
git commit -F - <<'EOF'
feat(cli): add the export subcommand [BOT]

Explain why, wrapped at 72 columns.
EOF
```

Nothing follows the terminator. Never put `&& \` on the line that opens the
heredoc: the continuation pulls the next line into the command, the body starts
a line late, and the message that reaches the commit is garbled.

Where the harness runs the `PreToolUse` git guard, it rejects a command holding
more than one `git commit`, and a `git commit` that follows a heredoc opened on
a continued line. The rule holds either way.

## Message format

Every commit **you** author carries the `[BOT]` marker, so a human reader can
tell agent-authored commits from hand-authored ones at a glance.

```
<type>(<scope>): <subject> [BOT]

<optional body: why, not what, wrapped at 72 columns>
```

- **type**: feat fix docs refactor perf test build ci chore data
- **scope**: optional, a real part of this repository
- **subject**: imperative ("add", not "added" or "adds"), lowercase first
  letter, no trailing period
- **`[BOT]`**: literal, uppercase, the **last** token of the subject line
- subject line 72 characters or fewer, including `[BOT]`

```
fix(nav): keep the mobile menu open across route changes [BOT]
docs(readme): document the preview-build environment variables [BOT]
refactor(theme): move colour tokens into a single CSS layer [BOT]
```

Not acceptable: `update`, `wip`, `fixes`, `Update Nav.tsx`, anything in past
tense, anything without `[BOT]`.

The marker goes at the end rather than the start so the subject stays parseable
as a conventional commit, which is what changelog generators and release
tooling expect at position 0.

Check before every commit, after staging:

```bash
scripts/hooks/no-local-paths
scripts/hooks/validate-commit-msg.sh -m "fix(api): guard empty metadata [BOT]"
```

pre-commit runs the same checks where it is installed, but not every clone has
it. A non-zero exit means do not commit. Fix the cause and check again. Where
the harness runs the `PreToolUse` git guard, it also blocks an inline `-m`
message without `[BOT]`, and bulk staging. Treat a block as a correct catch,
not an obstacle to route around.
Never use `--no-verify`.

**Do not add trailers.** No `Co-Authored-By:`, no `Signed-off-by:`, no
generated-with footer. The `[BOT]` marker is the convention and it is
sufficient.

## Branch names

`<type>/<short-description>`: the type is one of the commit types above, the
description lowercase and hyphenated. A branch that spans several types takes
its main one. For example:

```
fix/live-suite
chore/react-19
refactor/schema-decoupling
```

## Commit identity comes from the clone's git config

The commit identity for this repository is set in the clone's own git config,
which nothing commits, so a teammate who clones it never inherits someone
else's address or key:

```sh
git config --local user.name "<your name>"
git config --local user.email <your-email>
git config --local user.signingkey <key-id>
git config --local commit.gpgsign true
git config --local tag.gpgsign true
```

**When the clone's own config sets `user.name`, `user.email` and
`user.signingkey`, and `commit.gpgsign` to `true`, commit with it and do not
ask.** Check each with `git config --local --get <key>`. A value set there is a
decision about this repository; a missing one falls back to the global config,
where `commit.gpgsign=false` commits unsigned without any error. `tag.gpgsign`
gates tags, not commits.

**When any of them is missing, stop and ask which account and which GPG key to use,
and wait for an answer.** Never infer it from whatever `git config` happens to
return: a commit attributed to the wrong account or signed with the wrong key
cannot be corrected without rewriting history. Ask once per session and reuse
the answer for the remaining commits of that session.

Never create, edit or import a GPG key. Never change the global git config.
Never work around a missing identity with `git -c user.email=...`. If signing
fails, stop and report it rather than committing unsigned.

## Never reach for the `gh` CLI

Do not run any `gh` command unless the user asked for that specific command, or
you offered alternatives and they picked one. This covers reads (`gh pr list`,
`gh run view`) as well as writes.

The default for anything GitHub-side is to prepare the material and hand it
over: write the PR title and body to a `bot-*.md` note in `.temp/`, give the
`pull/new/<branch>` URL, and let the user open it in the browser. Same for
issues, reviews and releases. `gh` acts under the user's own credentials on a
shared remote, and a PR opened from here is visible to everyone watching the
repository before they have read a word of it.

**This is about the CLI, not about GitHub.** Writing and committing Actions
workflows is approved standing infrastructure. Do not hedge them: wire the real
trigger rather than reaching for `workflow_dispatch` to keep a build from going
red before its secrets exist. The distinction is who acts. A committed workflow
runs later, under the repository's own credentials, after the user has pushed
it. `gh` acts *now*, as them, on something they have not seen.

### Shape of a handoff note

Two parts, split at the first `---`. Above the rule: branch state, the
`pull/new/` URL, the title in a fenced block to copy, and anything the user
should know *before* sending, including caveats, omissions and questions.
Below the rule: the PR body and nothing else, provenance line included, so it
can be selected in one go and pasted into the description box without editing.
Nothing that is not part of the PR body may appear below the rule. Headings
inside the body start at `##`.

### What goes in a PR

Write it so a reviewer knows what changed, why, and how it was checked within
30 seconds of reading.

**Title:** the commit subject format, `<type>(<scope>): <subject> [BOT]`. For a
series of commits, name the change the series makes, not its last commit.

**Body**, in this order, then the provenance line described below:

```markdown
## Why

One to three sentences: the problem, and why this is the fix.

## Changes

- One line per commit, in commit order.

## Verified

- The command run and its result, e.g. `pytest`: 42 passed.
```

- **Why** is the part the diff cannot show. Do not narrate the diff, and do not
  repeat what the commit bodies or code comments already say.
- **Verified** lists the checks this repository defines (its tests, linters,
  build) that were actually run, with their results. A check that was skipped
  is listed as skipped, with the reason. If a behaviour change has no automated
  check, say how it was checked by hand.
- Add a section only when the repository's own rules ask for one.
- Keep the body under about 200 words. If it needs more, the PR covers more
  than one topic: split it rather than lengthening the description.

**Size:** one topic per PR, as for commits. An unrelated fix found on the way
goes in its own PR.

### PR descriptions carry a provenance line

Last thing in the body, after a `---` rule:

```markdown
---

PR text drafted by <AGENT NAME> (<EXACT MODEL ID OF THE SESSION>), reviewed by
the author.
```

Fill in the tool that actually wrote the text and the exact model ID of that
session, not a family name. If a PR description turns out to be wrong a year
from now, the only useful question is which model produced it, and only the
exact ID answers that.

Keep it flat: no italics, no link, no first person, no sentence about
responsibility. "Reviewed by the author" already carries the ownership claim.
It applies to the body only, since the title carries `[BOT]`, and it does not
extend to commit messages, which take no trailers of any kind.

The line is only accurate once the author has read the text. That is why it
ships inside the `.temp/` note rather than going up through `gh`: the user
pastes it, so the user has seen it.
