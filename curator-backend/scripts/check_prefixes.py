#!/usr/bin/env python3
"""Check that the schema declares every prefix the project's TTL files use.

The CURIE prefix map is read from the schema's ``sh:declare`` declarations
(``rfdb_core.prefixes``). This verifies that every ``@prefix`` across
``schema/*.ttl`` and ``data/*.ttl`` (the large Glottolog dump included; only
headers are read) is declared there with the same namespace. Reports missing
and mismatched prefixes, and declarations not used by any file. Exits non-zero
on missing or mismatched, so it can gate a commit.

Run:
    cd curator-backend && uv run python scripts/check_prefixes.py
"""

from pathlib import Path

from rdflib import Graph

from rfdb_core.prefixes import declared_prefixes, scan_ttl_prefixes

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "schema.ttl"


def main() -> int:
    ttl_files = sorted(
        str(p) for p in [*REPO_ROOT.glob("schema/*.ttl"), *REPO_ROOT.glob("data/*.ttl")]
    )
    used = scan_ttl_prefixes(ttl_files)
    declared = declared_prefixes(Graph().parse(SCHEMA_PATH, format="turtle"))

    missing = {p: ns for p, ns in used.items() if p not in declared}
    mismatched = {
        p: (ns, declared[p]) for p, ns in used.items() if p in declared and declared[p] != ns
    }
    unused = sorted(set(declared) - set(used))

    print(
        f"Scanned {len(ttl_files)} TTL files; {len(used)} prefixes used, {len(declared)} declared."
    )
    if missing:
        print("\nMISSING from the schema's sh:declare (used in TTL, not declared):")
        for prefix, ns in sorted(missing.items()):
            print(f"    {prefix}: {ns}")
    if mismatched:
        print("\nMISMATCHED namespace (TTL @prefix != sh:declare):")
        for prefix, (used_ns, declared_ns) in sorted(mismatched.items()):
            print(f"    {prefix}: TTL={used_ns}  sh:declare={declared_ns}")
    if unused:
        print("\nDeclared but not used by any scanned TTL (ok if intentional):")
        print("    " + ", ".join(unused))

    if not missing and not mismatched:
        print("\nOK: every used prefix is declared with a matching namespace.")
        return 0
    print("\nFAIL: declare the prefix in the schema (sh:declare), upstream for a copied schema.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
