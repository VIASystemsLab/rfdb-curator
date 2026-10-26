# AGENTS.md: rfdb-curator

RossijskijFeatrDB (rfdb): a curated RDF knowledge base of Russian theatrical works and their libretti, served by a SHACL-driven curator/explorer stack.

**Read [.agent-defs/00-core.md](.agent-defs/00-core.md) before acting on or
planning any task.**

This file plus `.agent-defs/` is the whole instruction set, and it is
vendor-neutral on purpose: a rule worth writing down is worth writing once,
where every tool reads it. Do not put rules in a per-tool file, and do not
commit one if your tool generates it. A gitignored `CLAUDE.md` that does nothing
but import this file is fine, since it carries no rules of its own.

## Other instruction files

One may exist anyway. Look for each of these in the repository root and read
every one you find: `CLAUDE.md`, `.claude/rules/`, `GEMINI.md`, `.cursor/rules/`, `.cursorrules`, `.github/copilot-instructions.md`, `.windsurfrules`. Where one differs from
`.agent-defs/` only in how strict it is, follow the stricter rule. Where the two
cannot both be followed, stop and ask which one applies.

## Modules

| File | Covers |
| - | - |
| [00-core.md](.agent-defs/00-core.md) | The binding rules in short form. Read first. |
| [git-workflow.md](.agent-defs/git-workflow.md) | Permission, staging, commit format, commit identity, GitHub handoffs |
| [temp-files.md](.agent-defs/temp-files.md) | `.temp/` scratch space, and never citing a local-only path |
| [security.md](.agent-defs/security.md) | Secrets, credentials, network access |
| [working-style.md](.agent-defs/working-style.md) | Scope, when to ask, verifying, reporting |
| [code-style.md](.agent-defs/code-style.md) | Conventions, dependencies, version bumps |
| [language.md](.agent-defs/language.md) | How to write prose in this repository |
| [upgrades.md](.agent-defs/upgrades.md) | Checking, running and verifying version upgrades |
| [platform.md](.agent-defs/platform.md) | Docker Compose services with application code and persistent state. |
| [python-package.md](.agent-defs/python-package.md) | A Python package or a set of scripts, installed and tested locally. |
| [repo-specific.md](.agent-defs/repo-specific.md) | Rules specific to this repository, kept through migration |
| [overview.md](.agent-defs/overview.md) | Architecture and module map. Durable facts only. |

## Local-only files

`CLAUDE.md` and `.claude/` are gitignored. They hold machine-local harness
configuration, such as the permission rules that mechanically back the rules
above, and a fresh clone has neither. Nothing in them is part of the project,
and nothing in them may contradict `.agent-defs/`.
