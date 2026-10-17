# schema/

`schema.ttl` is the SHACL schema this repository runs with: the RossijskijFeatr
(rfdb) model, used by the services, the tests and the demo. It is maintained
upstream and copied here from a tagged release, together with the vocabulary and
example data that go with it. Model changes go upstream, not into these files.

## Copied version

| | |
| - | - |
| Upstream | <https://github.com/UniVR-DH/rfdb-schema> |
| Release | `v2.2.2` (`owl:versionInfo "2.2.2"` in `schema.ttl`) |
| Commit | `6a6c74f3d98dd1258ce658751f75624600129e8f` |
| Copied | 2026-10-17 |

| Local file | Upstream path | Git blob id at the release |
| - | - | - |
| `schema/schema.ttl` | `schema/schema.ttl` | `75bfc4c7747aa7e9eda1e579a1e6ef8799abd3d7` |
| `data/vocab.ttl` | `data/vocab.ttl` | `80729d80ba52785ca71d055154b6ead69b95ed8a` |
| `data/data.ttl` | `data/examples/data.ttl` | `92468a8cfd0238b339ed5bf89ed1c818458dac48` |

## Checking the copy

Each local file must hash to the blob id in the table, and `owl:versionInfo` in
`schema.ttl` must match the release. From the repository root:

```sh
git hash-object schema/schema.ttl data/vocab.ttl data/data.ttl
```

To confirm the ids against upstream, run this in a clone of the upstream
repository, with the release tag in place of `<tag>`:

```sh
git ls-tree <tag> schema/schema.ttl data/vocab.ttl data/examples/data.ttl
```

## Updating to a newer release

1. Read the upstream `CHANGELOG.md` between the copied release and the new one.
   A MAJOR release changes what validates.
2. Copy the three files from the new tag, using the paths in the table.
3. Update both tables above.
4. Run the three test suites (see [docs/development.md](../docs/development.md)).
5. Commit the files and this record together, as one change.

## Using another schema

Replace `schema.ttl`, or point `SCHEMA_PATH` at another file. The services read
the CURIE prefix map from the schema's `sh:declare` declarations, so declare
there every prefix the schema and the data files use.
