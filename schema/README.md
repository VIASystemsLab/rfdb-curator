# schema/

`rfdb_schema.ttl` is the SHACL schema this repository runs with: the RossijskijFeatr
(rfdb) model, used by the services, the tests and the demo. It is maintained
upstream and copied here from a tagged release, together with the vocabulary and
example data that go with it. Model changes go upstream, not into these files.

## Copied version

| | |
| - | - |
| Upstream | <https://github.com/VIASystemsLab/rfdb-schema> |
| Release | `v2.2.2` (`owl:versionInfo "2.2.2"` in `rfdb_schema.ttl`) |
| Commit | `6a6c74f3d98dd1258ce658751f75624600129e8f` |
| Copied | 2026-10-17 |

| Local file | Upstream path | Git blob id at the release |
| - | - | - |
| `schema/rfdb_schema.ttl` | `schema/schema.ttl` | `75bfc4c7747aa7e9eda1e579a1e6ef8799abd3d7` |
| `data/rfdb_vocab.ttl` | `data/vocab.ttl` | `80729d80ba52785ca71d055154b6ead69b95ed8a` |
| `data/rfdb_data.ttl` | `data/examples/data.ttl` | `92468a8cfd0238b339ed5bf89ed1c818458dac48` |

## Checking the copy

Each local file must hash to the blob id in the table, and `owl:versionInfo` in
`rfdb_schema.ttl` must match the release. From the repository root:

```sh
git hash-object schema/rfdb_schema.ttl data/rfdb_vocab.ttl data/rfdb_data.ttl
```

To confirm the ids against upstream, run this in a clone of the upstream
repository, with the release tag in place of `<tag>`:

```sh
git ls-tree <tag> schema/schema.ttl data/vocab.ttl data/examples/data.ttl
```

## Updating to a newer release

1. Read the upstream `CHANGELOG.md` between the copied release and the new one.
   A MAJOR release changes what validates.
2. Copy the three files from the new tag. The local names differ from the
   upstream paths (each carries the `rfdb_` prefix, so a fork's own files can sit
   next to them), so copy each upstream path to its local name from the table.
   With a clone of the upstream repository at `<clone>` and the new tag in place
   of `<tag>`, from the repository root:

   ```sh
   git -C <clone> show <tag>:schema/schema.ttl > schema/rfdb_schema.ttl
   git -C <clone> show <tag>:data/vocab.ttl > data/rfdb_vocab.ttl
   git -C <clone> show <tag>:data/examples/data.ttl > data/rfdb_data.ttl
   ```

   `git show` writes the files exactly as committed at the tag, so their blob
   ids match the ones `git ls-tree` reports there.
3. Update both tables above.
4. Run the three test suites (see [docs/development.md](../docs/development.md)).
5. Commit the files and this record together, as one change.

## Using another schema

Add your own schema file next to `rfdb_schema.ttl` and point `SCHEMA_PATH` at it
(see [docs/custom-schema.md](../docs/custom-schema.md)). The services read
the CURIE prefix map from the schema's `sh:declare` declarations, so declare
there every prefix the schema and the data files use.
