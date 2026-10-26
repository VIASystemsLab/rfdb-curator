"""Pydantic request/response models for the RossijskijFeatrDB API.

Modules
-------
data    EntityData, DataCreateResponse, ValidationResult, TripleObject — the
        write-path contract. The record-list read models live in
        ``dataexplorer-backend/models/data.py`` (only the reader returns them).
files   DigitalCopy — the staged-upload response. Its vocabulary constants are
        in ``rfdb_core.vocab``.
"""
