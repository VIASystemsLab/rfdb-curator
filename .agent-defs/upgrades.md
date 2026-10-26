# Upgrading runtimes, dependencies and tools

How to check, run and verify a version change: a language runtime, a library, a
base image, a CI action, a build tool. The pin formats themselves are in
`platform.md` ("Images") and `repo-specific.md` ("GitHub Actions and CI").

## Before changing anything

- **Find every place the version is stated.** Manifests, lockfiles, container
  images, CI jobs, scripts, docs and comments. Change them together. The
  environment that writes a lockfile and the one that installs from it must run
  the same tool version, or the lockfile churns or breaks.
- **Know where it runs.** Production runtime, build only, or development only.
  That decides the risk and which checks matter.
- **Check support status.** Whether the current version has reached end of
  life, and the target's release date, support window and stability (long-term
  support or not).
- **Follow the requirement chain.** Read the target's minimum versions of its
  runtime, peers and toolchain. Adopting one component can force upgrading
  another; find the whole chain first.
- **Read the release notes for every major version crossed.** List what was
  removed or changed, and search the code for each.
- **Use primary sources.** Versions, dates and support status come from the
  registry, the publisher's release page or the official documentation, never
  from memory. Note the date of each figure, and mark anything not verified.
- **Confirm the exact artifact exists before pinning it.** The tag, release or
  platform build. Publishers differ in what they tag: one publishes moving major
  tags, another only full versions. Never infer one tag from another.

## Making the change

- **One topic per commit.** A runtime upgrade, a dependency upgrade, a set of
  security fixes and a rule change are separate commits.
- **Use the package manager, in the environment CI matches.** Never edit a
  lockfile by hand.
- **Fix security findings inside the declared ranges first.** A fix that needs
  a new major version is a separate decision.
- **Read the lockfile diff.** Only the packages you meant to change, and their
  dependencies, should move. Explain or revert anything else.
- **Read new warnings and defaults.** A new tool version can add warnings,
  change defaults or require new approvals. Do not silence them; decide, or ask.
  An approval or exception that names an exact version has to be renewed on
  every update of that package.
- **Where a publisher does not offer the pin the rules ask for**, use the
  nearest form it does, record the exception next to the rule, and add a
  mechanism that flags the exception's new releases.

## Verifying

- **Check from a clean install of the lockfile**, not an incremental one.
- **Confirm that what runs is the new version.** Ask the running process, the
  container or what it serves for its version. Caches, reused volumes, old
  images and long-running processes keep the old version after the files have
  changed.
- **Test the artifact that ships**, not only the development setup: a
  production image or release build, started and requested.
- **Audit after the change**, and separate runtime findings from
  development-only ones.
- **What only CI or production can check stays unverified until it runs
  there.** Say so before pushing, and ask for the result.

## Afterwards

- Update the docs and comments that name the old version or the old rule.
- Keep upgrades routine: an audit on a schedule and on every lockfile change,
  security-update PRs, and version-update PRs for exact pins. Review an
  automated PR as you would a person's; never merge one unread.
