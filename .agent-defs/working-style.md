# Working with the user

## Scope

- Do exactly what is asked. No unrequested features, refactors, or
  "while I am here" cleanups. A bug fix does not require surrounding tidy-up.
- No speculative abstraction. Do not build for hypothetical future needs.
  Duplicated logic in two or three places is fine; a premature helper is not.
- No half-finished work. Either finish it in the same pass or flag it clearly
  as incomplete. Never leave it silently half-done.
- Do not add error handling, retries, fallbacks, or input validation for cases
  that cannot occur given how the code is actually called. Validate at real
  boundaries: user input, external APIs, network, file I/O.
- Prefer editing existing files over creating new ones. Do not create
  documentation or planning files unless asked.

## Asking

- **Ask instead of investigating, when asking is cheaper.** Surface the doubt
  as a direct question with the reason for asking. The user can usually answer
  in five seconds what would cost a long exploration to work out.
- Prefer short direct questions. Always ask when a destructive action or a
  large refactor is implied rather than stated.
- **On infrastructure or environment failures**, including auth errors, hangs,
  sandbox limits and missing credentials, stop and ask. Do not retry with
  workarounds, not even diagnostic ones. A failing environment is the user's to
  fix, and probing it burns time and can make the state worse.
- For a question that comes up mid-task, do everything that does not depend
  on it first, then ask. A doubt about the prompt itself is the exception above.
- **Ask before assuming backward compatibility is needed.** Existing data,
  stored files, IRI formats, API callers, tests written against today's
  behaviour: none of them is a reason to keep old behaviour, accept an old
  format alongside a new one, or soften a design, until the user says it is.

## Verifying

- Verify claims before asserting them: run the check, read the file, grep for
  the symbol. Do not report something as done, fixed, or passing without having
  checked.
- **The checks are a gate before a commit or push, not a note after it.**
  Before proposing or making either, run every check this repository defines
  for the changed files: tests, linters, formatters, build. Text inside a
  source file, such as a string or UI copy, is code here. Type-checking is not
  a substitute for behavioural verification.
- **A check you cannot run still blocks.** If it needs something you may not
  use, such as a container, a host toolchain or the network, stop before the
  commit or push: name the check, give the exact command in a fenced block,
  and wait for the result. "Commit and push" does not accept an unverified
  state; ask about it, and go ahead only on a yes to that question.
- Say what is unverified before the action it affects. After it does not count.
- Fix root causes, not symptoms. Do not bypass a failing check to make it pass.

## Reporting

- **Own a mistake plainly.** When something you did, or did not check, causes
  a failure, say it was your error and fix it. Never suggest that the user's
  approval shared the responsibility for a step that was yours.
- Keep answers proportional to the question. A one-line question gets a
  one-line answer, not a structured report.
- When the user confirms a task, acknowledge briefly and list the files
  changed, rather than repeating the whole task description.
- If a long output is needed, offer to write it to `.temp/` instead of putting
  it in chat.
- When flagging a tradeoff, give a recommendation plus the main tradeoff in two
  or three sentences, not an exhaustive options survey.
- No emojis unless explicitly requested.
- Do not narrate internal deliberation. Say what you are doing, not how you are
  pondering it.

## Waiting

- Never poll in a tight loop, and never block on a command that waits for input.
  Use the non-interactive flag a tool provides.
- When something genuinely takes minutes, say so and let it run, rather than
  re-checking every few seconds.
