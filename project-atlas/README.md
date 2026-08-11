# project-atlas

An Agent Skill that turns every project directory on a machine into one durable, searchable
SQLite index — and replicates that index to S3, Cloudflare R2, Backblaze B2, or a local
disk with [Litestream](https://github.com/benbjohnson/litestream), restore included.

The problem it solves: a developer machine ends up with hundreds of checkouts scattered
across `~/src`, `~/work`, and forgotten one-off clones. "Where is that repo with the Stripe
webhook?" and "which checkouts still point at the old forge?" become `find` plus guesswork —
and the answer dies with the machine.

## Install

```sh
npx skills add dhanesh/agent-skills --skill project-atlas
```

## Use it

```sh
# what can this machine actually do?
python3 assets/atlas.py doctor

# look before you index
python3 assets/atlas.py scan --dry-run ~/src ~/work

# index (idempotent — run it as often as you like)
python3 assets/atlas.py scan ~/src ~/work
# SCAN_RESULT: OK (indexed=143 new=143 missing=0 total=143 engine=fts5 db=...)

# find things
python3 assets/atlas.py search "stripe AND webhook"
python3 assets/atlas.py search kafka --json

# make it survive the machine
python3 assets/atlas.py litestream-config --target r2 \
    --bucket atlas-index --account-id $CF_ACCOUNT_ID \
    --out ~/.config/litestream/atlas.yml
litestream replicate -config ~/.config/litestream/atlas.yml
```

The config generator also prints the `litestream restore` commands for the replica it just
configured — latest, point-in-time, and dry-run.

## What gets indexed

One row per project root — a directory containing `.git` or a toolchain manifest
(`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, …). Vendor and build
trees (`node_modules`, `.venv`, `target`, `vendor`, …) are never walked, and a lone
`Makefile` does not make a directory a project.

Each row carries the absolute path, name, git remote/branch/HEAD/HEAD-timestamp, detected
languages and toolchains, file count and size, and three timestamps: `first_seen`,
`last_scanned`, `missing_since`. **Projects that disappear are tombstoned, not deleted** —
knowing where something *used to be* is half the value of an inventory.

Search runs over each project's path, README, and top-level docs, capped at 64 KiB per
project, via SQLite FTS5 — with a substring fallback that prints the same output and exits 0
on builds without FTS5.

## Design decisions worth knowing

- **The index is always WAL.** Litestream replicates from the write-ahead log and forces
  `journal_mode = wal` on the database it is given; opening WAL up front turns a
  replication-time failure into an immediate, readable one.
- **Generated configs target Litestream 0.5.x.** The singular `replica:` mapping, not the
  deprecated `replicas:` list that most tutorials still show.
- **R2 endpoints always carry `https://`.** Litestream's R2 detection — signed payloads,
  `concurrency=2`, checksums off — keys on the scheme. Without it the replica runs with AWS
  defaults against a backend that rejects them.
- **`sync-interval` is always explicit.** Litestream's default is 1s; for an index that
  changes when you run a scan, that is a lot of API calls for nothing.
- **No secrets on disk.** Credentials are emitted as `${ENV}` expansions, and the generator
  refuses to write a config containing a live credential value from the environment.

Each of these traces to a source fetched and quoted in
[`docs/project-atlas/2026-08-11-research-validation.md`](../docs/project-atlas/2026-08-11-research-validation.md),
which also records the one claim that could **not** be grounded (whether FTS5 is present in
every SQLite build) and why the code probes for it instead of assuming.

## Not this

Not a code-search engine over whole source trees — it indexes metadata, paths, and docs; use
ripgrep against the paths it gives you. Not a daemon or file-system watcher — scans are
invoked, and only Litestream runs continuously. Not a Cloudflare Durable Object / Worker
deployment. Not a secrets store.

## Layout

```
project-atlas/
  SKILL.md                  agent-facing prompt
  README.md                 this file
  assets/atlas.py           scanner, search CLI, doctor, config generator (stdlib only)
  assets/test_atlas.py      47-check unit suite
  eval/run_eval.py          end-to-end outcome eval (28 checks, negative fixtures included)
  references/parameters.md  every flag, exit code, environment variable
  references/schema.md      table definitions and SQL recipes
  references/replication.md Litestream specifics, per-provider settings, the restore drill
```

## Development

```sh
make gate-skill SKILL=project-atlas       # structure, frontmatter, leaks, playbook, tests, eval
python3 -m unittest discover -s project-atlas/assets -p 'test_*.py'
python3 project-atlas/eval/run_eval.py
```

The spec and task plan this skill was built from are in
[`docs/project-atlas/`](../docs/project-atlas/).
