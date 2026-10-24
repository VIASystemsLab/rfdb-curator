"""Core services for the RossijskijFeatrDB backend.

Modules
-------
config            Curator settings; extends rfdb_core.config.BaseServiceSettings.
shacl_validator   Wraps pyshacl for pre-write SHACL validation.
validation_merge   Plans the CONSTRUCT that pulls in referenced entities.
seeder            bootstrap_store(): wait for the store, optionally wipe it,
                  load the vocabulary files (and optionally the sample data). Called by the
                  lifespan and by scripts/seed.py.
blank_node_handler Skolemizes blank nodes before validation and storage.

The triplestore client, schema extractor, prefix map, object storage and logging
setup all live in the shared ``rfdb_core`` package.
"""
