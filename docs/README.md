# RFDB Curator — Documentation

Project documentation for `rfdb-curator`. The root `README.md` remains the entry point
for a quick overview, setup, and the configuration variables; these
files go deeper on specific topics. 

| Document | Covers |
|---|---|
| [getting-started.md](getting-started.md) | What the editor is for, the WEMI data model in brief, and how to run it locally and in production. |
| [data-model.md](data-model.md) | The data-model contract: what any schema must provide for this application (shape roles, constraints the editor understands, identifiers, uploads, schema metadata), and the rfdb schema this repository runs. |
| [architecture.md](architecture.md) | System design: the schema-driven pipeline, the writer/reader service split and what each owns, the API endpoint reference, SHACL extraction, validation and delete behavior, the metadata API, and the Oxigraph/Garage storage stack. |
| [development.md](development.md) | Development workflow: environment setup, code quality, CI, schema and data change workflows, troubleshooting, logs, and the commit checklist. |
| [deployment.md](deployment.md) | Deployment & operations: development/testing configuration, data-reset modes, and seed sources; plus the production deployment plan (work in progress). |
| [custom-schema.md](custom-schema.md) | Running your own deployment: the checklist of what a fork sets (schema, base IRIs, graph, seed data, long-text fields, Welcome guide) and where. |

