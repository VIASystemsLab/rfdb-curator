"""Field and shape labels prefer English when the schema gives several languages.

The schema names every field in English and Italian (``sh:name``). Reading one
value with ``Graph.value`` returns an arbitrary language, so without a preference
an English UI could show "Identificativo RISM".
"""

from __future__ import annotations

from pathlib import Path

from rfdb_core.schema_extractor import SchemaExtractor

_SCHEMA = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <http://example.org/> .

ex:ThingShape a sh:NodeShape ;
  sh:targetClass ex:Thing ;
  sh:name "Cosa"@it, "Thing"@en ;
  sh:property [ sh:path rdfs:label ; sh:datatype xsd:string ] ;
  sh:property [
    sh:path ex:rism ;
    sh:name "Identificativo RISM"@it, "RISM ID"@en ;
    sh:datatype xsd:string ;
  ] ;
  sh:property [ sh:path ex:plain ; sh:name "Nome"@it, "Plain name" ; sh:datatype xsd:string ] ;
  sh:property [ sh:path ex:italian ; sh:name "Solo italiano"@it ; sh:datatype xsd:string ] .
"""


def _fields(tmp_path: Path) -> tuple[dict, dict[str, str]]:
    schema = tmp_path / "schema.ttl"
    schema.write_text(_SCHEMA, encoding="utf-8")
    shape = SchemaExtractor(str(schema)).get_shape("http://example.org/ThingShape")
    assert shape is not None
    return shape, {p["pathUri"]: p["name"] for p in shape["properties"]}


def test_field_name_prefers_english(tmp_path: Path) -> None:
    _, names = _fields(tmp_path)
    assert names["http://example.org/rism"] == "RISM ID"


def test_field_name_falls_back_to_untagged_then_any(tmp_path: Path) -> None:
    _, names = _fields(tmp_path)
    assert names["http://example.org/plain"] == "Plain name"
    assert names["http://example.org/italian"] == "Solo italiano"


def test_shape_label_prefers_english_sh_name(tmp_path: Path) -> None:
    shape, _ = _fields(tmp_path)
    assert shape["label"] == "Thing"
