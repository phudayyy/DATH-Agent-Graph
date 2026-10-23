# Provenance

Every file listed here was copied **byte for byte** from the author's pre-existing private system, finch-brain, at commit
`467022f9e04d1c0ab2ddc5a481faaec7da5dac28` — the last commit on its `main` by the end of 2026-09-28 (UTC+7), the
baseline frozen for this project. The commit that adds this file adds nothing else.

Check any file against the baseline: `git hash-object <path>` at that commit must print the blob below.

| Path | Git blob at the baseline | Lines |
|---|---|---|
| `src/brain/__init__.py` | `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` | 0 |
| `src/brain/clock.py` | `debc5158c1d61f29b1011adf0f68cf4efebe0a5c` | 59 |
| `src/brain/platforms.py` | `e14260bdec361c88af501bdd319d9f5340210d00` | 476 |
| `src/brain/retrieve/__init__.py` | `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` | 0 |
| `src/brain/retrieve/acl.py` | `8a60df8e0ef214ce56c3dd302c4a20d832bc3ec6` | 155 |
| `src/brain/retrieve/pack.py` | `bda60cc36460998c41e0c021d304e06790c0adf4` | 1497 |
| `src/brain/resolve/__init__.py` | `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391` | 0 |
| `src/brain/resolve/hardkey.py` | `01b8b8c131f060f4d945fdf421858f944dd88eca` | 362 |
| `migrations/versions/0001_initial_schema.py` | `cda143212ab50b86015471c76bcee7512a751a38` | 414 |

Every later change to these files is work done for this project — `git log -p --follow <path>` shows each one.

## Cut down to what CRONQUESTIONS needs

The commit after the import keeps only what the dataset exercises — 2,963 imported lines become 633 — and
moves the schema out of an Alembic migration into one SQL file:

| File | Kept | Removed |
|---|---|---|
| `src/brain/clock.py` | `utc_now`, `ensure_utc`, `require_utc` | `is_before_or_equal` |
| `src/brain/platforms.py` | the ACL predicate, built from the list of entity kinds | 26 platform manifests; 14 entity kinds become 3 (`person`, `group`, `item`) |
| `src/brain/retrieve/acl.py` | all of it | — |
| `src/brain/retrieve/pack.py` | the pack, its line format, `GAPS`, the permission cut before anything is trimmed | passages, facts, staleness, reranking; the token budget becomes a cap on lines |
| `src/brain/resolve/hardkey.py` | `normalize_email` | entity resolution for every platform |
| `migrations/versions/0001_initial_schema.py` | `tenants`, `entities`, `edges`, `acl`, now in `src/agent_graph/schema.sql` | every other table; the `btree_gist` overlap constraint — the loader merges overlapping intervals instead |

Everything under `src/agent_graph/` other than `schema.sql`, and everything under `tests/`, was written for this project.
