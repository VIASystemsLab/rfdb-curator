# Platform conventions

A platform repository runs services, holds state, and can destroy hours of work
with one command. The rules below are about that, not about style.

## Never do these on your own initiative

- `docker compose down -v`. The `-v` drops named volumes, which is the database.
- Anything that writes into a data, storage or cache directory.
- A full re-index, re-encode, or bulk download. These cost hours and gigabytes.
- Deploying, publishing, or promoting a build to any live environment.

Describe the command and let the user run it.

Cheap and fine without asking: `docker compose ps`, `docker compose config`,
`docker compose logs`, a health-check `curl` against localhost. The permission
rules still prompt for that `curl`, because they cannot tell localhost from any
other host.

## The Docker daemon is the user's

**Never start or restart the Docker daemon or Docker Desktop yourself.** If the
engine is down, say so and ask. Running `docker compose` against a live daemon
is fine; launching the engine is the user's prerogative.

## Untrusted code stays in the container

**Never run Node, npm or npx on the host.** All JavaScript execution and
JavaScript dependency installation happen inside a container. Package install
scripts execute arbitrary code, and the host has the credentials. Any other
package install on the host needs a yes first; where the python-package rules
apply, they say how.

## Images

Pin every image tag. Never `latest`, never an open-ended range. Which form each
pin takes is in `repo-specific.md`, "Version pins". Secrets and `.env` follow
`security.md`.

## Compose files

The compose files carry the deployment values; keep each equal to the code's
default for that setting (the general rule is in `code-style.md`, configuration).
When they drift, the service works locally and fails in deployment, which is the
worst way to find out.
