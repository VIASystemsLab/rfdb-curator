# Core rules

These rules are binding. They override any general default behaviour. When a
rule here conflicts with what seems convenient, the rule wins.

1. **Check every prompt before acting on it.** If it is ambiguous, looks
   incomplete (a pasted brief that stops at an empty heading, a sentence cut
   off), or looks meant for a different conversation, stop and ask before
   reasoning, reading or running anything. Work built on a half-stated request
   is usually redone once the rest arrives.
2. **Never touch anything outside this repository.** Not sibling
   repositories, not `$HOME` dotfiles, not system paths, not global installs.
   Read-only inspection outside the repository (reading a config file,
   `--version`, `command -v`) is not allowed either without asking first. The
   one exception is the session scratchpad directory, for throwaway files that
   must not touch the project. If a task genuinely requires a change outside
   the repository, stop and say so. Do not do it and report it afterwards.
3. **Never `git commit` without explicit permission**, every time. "The change
   is finished" is not permission to commit, and permission for one commit does
   not carry over to the next.
4. **Silence is never a yes.** Only an explicit answer to the question asked
   counts as one. If a reply skips a question, or answers a different one, ask
   it again before acting on it.
5. **The default branch is never the default choice.** Look at which branch
   HEAD is on before the first change, and before the first commit ask which
   branch the work goes on, naming the one you propose. Never switch branch
   without a yes.
6. **Never stage in bulk.** No `git add -A`, `git add .`, `git add -u`,
   `git commit -a`. Stage an explicit, enumerated list of paths.
7. **One commit per topic**, and every commit you author ends its subject line
   with `[BOT]`. Never a commit called "update".
8. **`.temp/` is local scratch space, excluded in `.git/info/exclude`.** Read it
   freely, say which file and why before writing, never overwrite an existing
   file without a named yes, never commit it, and never point a committed file
   at anything in it.
9. **Size-check before reading any file.** A large generated or downloaded file
   saturates the context window and blocks the user's work. Check the size
   first (`wc -l`, `ls -la`), and for anything large extract only what you need
   with targeted `grep`, `sed -n` or `head`.
10. **Never start a long-running or destructive operation on your own
    initiative.** Describe the command and let the user run it.
11. **Ask instead of investigating, when asking is cheaper.** A direct question
    with the reason for asking usually costs the user five seconds and saves a
    long exploration.
12. **Be brief.** Answer the specific question and stop. No unrequested
    background, no restating what was just agreed, no defensive caveats.
13. **Never reach the network without a yes**, every time. Any request to a
    host other than localhost needs one: a fetch, a web search, `git fetch`,
    `pull` or `clone`, a package install or sync. Name the URL, what is sent,
    why, and whether it is required or nice to have, then wait. The full rule
    is under "Network access" in the security rules.
