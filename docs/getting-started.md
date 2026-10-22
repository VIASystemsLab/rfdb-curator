# Getting Started with RFDB Curator

A short orientation: what the editor is for, the shape of the data model you will be
working with, and how to run it locally. For what the application requires of a schema see
[data-model.md](data-model.md); for running a development environment see
[development.md](development.md); for configuration, data seeding, the deploy modes, and the
production runbook see [deployment.md](deployment.md).

---

## What this application is for

RFDB Curator is a curation application for RossijskijFeatrDB. It lets you create,
edit, validate, and inspect records about musical works, their librettos and editions,
the physical sources that hold them, and the people, places, roles, and performances
connected to them.

Every record type, field, and constraint in the editor is generated automatically from
an underlying SHACL schema, so the forms always reflect the current data model. You do
not need to edit RDF by hand — you fill in forms, and the application produces
validated RDF behind the scenes.

---

## The data model, in brief

The records follow a layered "work → expression → manifestation → item" (WEMI)
structure. Each level is more concrete than the one above it:

- **Musical Work** — the abstract work (e.g. an opera as a creative idea).
- **Expression** — an intellectual realization of the work, such as its libretto.
- **Manifestation** — a specific edition or product type of an expression.
- **Source / Item** — a single physical or documentary copy held by an institution.

Around this spine sit the supporting records you link to: **Persons**, **Roles**,
**Places**, **Subjects**, **Source Types**, **Holding Organizations**, and staged
**Performances**. Contributor roles (composer, librettist, and so on) are attached
through small **Agent Role** bridge records that connect a person to a role.

### Recommended editorial order

Because every link points from the more concrete record up to its parent, create
parents before children:

1. Create the auxiliary entities first (Places, Persons, Subjects, Organizations, and
   the document Types) — they populate the dropdowns used below.
2. Create the Musical Work.
3. Create the Expression (e.g. the libretto), linked up to its parent Work.
4. Create the Manifestation, linked to the Expression.
5. Create the Source / Item, linked to the Manifestation (one Manifestation can have
   many Sources).

The editor also supports incremental cascade insertion — you can create a parent and
inline-create its nested referenced entities in one step. A built-in "Getting started"
overlay in the app walks through this WEMI flow the first time you open it.

---

## Running it locally

The full local setup — prerequisites, the one-time `.env` + Garage bootstrap, day-to-day
run/stop commands, service URLs, and the data-reset and seeding options — is documented in
the [Quick Start](../README.md#quick-start) section of the root `README.md`.

In short: run `scripts/env-init.sh`, `docker compose up -d --build`, and
`scripts/garage-init.sh` once on a fresh checkout, then open the editor at
<http://localhost:5173>.

---

## Running in production

**Nothing has been deployed yet**, but the stack to do it with is complete: a hardened Docker
Compose stack behind a TLS-terminating reverse proxy, with the triple store and object store
kept internal-only. The whole topology has been exercised locally — both frontend production
images build and the real Caddyfile was run against them route by route — so what is missing is
a host, a domain and a certificate rather than any code. Step-by-step runbook, and the one known
gap (log rotation): [production deployment](deployment.md#production-deployment).
