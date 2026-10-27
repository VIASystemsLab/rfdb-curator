# Planning Documents

Design and implementation plans for non-trivial features live in `.temp/` and follow a
fixed format so they read consistently and track completion. Before implementing a
multi-step feature, write one, with prior approval like any new file; keep it updated
as work lands.

## Location & Naming

- Directory: `.temp/`, excluded from git in `.git/info/exclude`: plans are working artifacts, not tracked docs.
- In progress: `bot-{YYYYMMDD}-{purpose}.md` when an agent writes it, `temp-{YYYYMMDD}-{purpose}.md` when the user does (lowercase, hyphenated purpose; see [temp-files.md](temp-files.md)).
- On completion: rename with a `DONE-` marker → `bot-DONE-{YYYYMMDD}-{purpose}.md` (or `temp-DONE-…`). Plans named the old way keep their names.
- The date is the creation date and stays fixed through the rename.

## Header Block

Every plan opens with a title and a metadata list:

```markdown
# Plan: <Feature Name>
- **Created:** YYYY-MM-DD
- **Author:** <name or agent>
- **Status:** Planned | In progress | Complete
```

## Required Sections

1. **Progress Tracker:** a checklist of milestones/tasks (`- [ ]` / `- [x]`) mirroring
   the Task sections below. This is the at-a-glance status; keep the boxes current.
2. **Problem Statement:** what is broken/missing and why it matters. Name the concrete
   failure mode (silent data loss, opaque config, duplication) rather than restating the
   feature title.
3. **Design:** the chosen approach, with response shapes, data flow, key decisions, and the
   reasoning that rules out alternatives. Ground it in real files, routes, and existing
   patterns (cite paths), not abstractions. A **Requirements** subsection is optional
   when constraints are worth listing explicitly.
4. **Task N: <short title>**, one per unit of work, each with:
   - **Files:** exact paths touched (mark `(new)`).
   - **Changes:** concrete, ordered edits; the smallest readable diff that works.
   - **Tests:** cases to add/extend (backend has pytest; frontend is manual smoke only).
   - **Demo:** an observable check proving the task works (a `curl`, a UI action).
5. **Non-goals:** what the plan deliberately does *not* do, to bound scope.

## Conventions

- Reconcile the plan against `TODO.md`: link the plan from the relevant TODO entry and
  mirror its milestone checkboxes so both stay in sync.
- Record deviations from the original sketch as **implementation notes** in the Task
  section when the built solution differs.
- Match the laziness rules in [repo-specific.md](repo-specific.md) → "The lazy veteran": plans propose the minimum custom code,
  reuse existing helpers/patterns, and justify any new dependency or abstraction.
