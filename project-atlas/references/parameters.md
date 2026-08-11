# CLI reference — `assets/atlas.py`

Stdlib-only python3. `--db` may be given before **or** after the subcommand; both forms
resolve to the same index.

## Index location

Resolved in this order:

1. `--db <path>`
2. `$ATLAS_DB`
3. `$XDG_DATA_HOME/project-atlas/atlas.db`
4. `~/.local/share/project-atlas/atlas.db`

The parent directory is created on demand. The file is always opened in WAL mode; if the
mode cannot be set, the command fails with exit 1 rather than producing a database
Litestream cannot replicate.

## `scan [ROOT ...]`

Discovers project roots and upserts them.

| Flag | Default | Effect |
|---|---|---|
| `ROOT ...` | `$HOME` | Directories to walk. Multiple roots are fine; each is walked independently. |
| `--depth N` | `8` | Maximum directory depth below each root. |
| `--nested` | off | Keep descending inside a project root (finds monorepo sub-packages; noisier). |
| `--dry-run` | off | Print `WOULD_INDEX:` lines and a `SCAN_RESULT: DRY-RUN` count. Writes nothing — not even the database file. |

**Discovery rules.** A directory is a project root when it contains `.git` or a *strong*
marker: `package.json`, `deno.json`, `pyproject.toml`, `setup.py`, `requirements.txt`,
`Pipfile`, `Cargo.toml`, `go.mod`, `Gemfile`, `pom.xml`, `build.gradle[.kts]`,
`composer.json`, `mix.exs`, `Package.swift`, `pubspec.yaml`, `CMakeLists.txt`, `flake.nix`.
*Weak* markers (`Makefile`, `Justfile`, `Dockerfile`, `docker-compose.yml`, `Taskfile.yml`,
`.pre-commit-config.yaml`) are recorded as toolchains but never promote a directory on their
own.

**Never descended into:** `.git`, `.hg`, `.svn`, `.jj`, `node_modules`, `bower_components`,
`vendor`, `.venv`, `venv`, `.tox`, `.nox`, `__pycache__`, `.mypy_cache`, `.pytest_cache`,
`.ruff_cache`, `target`, `dist`, `build`, `out`, `.next`, `.nuxt`, `.output`, `.gradle`,
`.idea`, `.vscode`, `.cache`, `.terraform`, `Pods`, `DerivedData`.

**Upsert semantics.** Rows are keyed on absolute path. A re-scan preserves `first_seen`,
refreshes every metadata column, moves `last_scanned`, and clears `missing_since`. A row
whose path is under a root just scanned but no longer exists on disk gets `missing_since`
set — and keeps its data. Rows outside the scanned roots are left alone, so scanning
`~/work` never tombstones `~/src`.

Output: `SCAN_RESULT: OK (indexed=N new=N missing=N total=N engine=fts5|like db=PATH)`.

## `search QUERY`

| Flag | Default | Effect |
|---|---|---|
| `--limit N` | `20` | Maximum hits. |
| `--no-fts` | off | Force the LIKE engine (useful for confirming fallback behaviour). |
| `--json` | off | Emit `{"engine": ..., "query": ..., "hits": [...]}` instead of lines. |

With FTS5 the query is an FTS5 MATCH expression: `stripe AND webhook`, `"exact phrase"`,
`pay*`, `NOT archived`. Without FTS5 — or when the MATCH expression is malformed, or when
`--no-fts` is passed — the query becomes a single escaped substring match, `%` and `_`
included literally. The fallback still exits 0 and prints the same `HIT:` lines; the
`engine=` field in `SEARCH_RESULT:` is how you tell which ran.

Output: `HIT: <path> — <name> [<languages>]` per hit (with ` [MISSING]` for tombstoned
projects), then `SEARCH_RESULT: N hit(s) (engine=fts5|like)`.

## `stats`

`--json` for a machine-readable summary. Otherwise `LANG:` lines for the top eight
languages, then `STATS_RESULT: projects=N git=N missing=N bytes=N`.

## `doctor`

| Flag | Default | Effect |
|---|---|---|
| `--json` | off | Emit the full probe as JSON. |
| `--require CAP` | none | Fail (exit 1) when a capability is absent. Repeatable, or comma-separated. Known: `fts5`, `wal`, `litestream`, `git`. |

Reports FTS5 availability, the index's journal mode (`NO-DB` when the index does not exist
yet), the `litestream` and `git` binaries, python and SQLite versions, and the project /
missing counts. An unknown capability name is an error, not a silent pass.

Output: `CHECK:` lines, then `DOCTOR_RESULT: PASS` or `DOCTOR_RESULT: FAIL (missing: ...)`.

## `litestream-config`

Generates a Litestream 0.5.x configuration and a restore runbook. See
`references/replication.md` for what each target needs and why.

| Flag | Default | Effect |
|---|---|---|
| `--target` | *required* | `s3`, `r2`, `b2`, `custom`, or `file`. |
| `--bucket` | — | Bucket name. Required for every target except `file`. |
| `--path` | `project-atlas` | Key prefix inside the bucket. |
| `--account-id` | — | Cloudflare account id; `r2` needs this or an explicit `--endpoint`. |
| `--endpoint` | — | S3-compatible endpoint. A missing scheme is upgraded to `https://`. |
| `--region` | `us-east-1` (s3), `auto` (r2) | Region. |
| `--replica-path` | — | Destination directory for `--target file`. |
| `--sync-interval` | `10s` | Always emitted explicitly; Litestream's own default is 1s. |
| `--out PATH` | — | Write the config here. Without it the YAML goes to **stdout** and the runbook to **stderr**, so `atlas.py litestream-config … > litestream.yml` yields a clean file. |

Refuses (exit 1) when the generated text would contain the value of `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `R2_SECRET_ACCESS_KEY`, `LITESTREAM_ACCESS_KEY_ID`, or
`LITESTREAM_SECRET_ACCESS_KEY` from the current environment.

## Environment variables

| Variable | Effect |
|---|---|
| `ATLAS_DB` | Default index path. |
| `XDG_DATA_HOME` | Base for the default index path. |
| `ATLAS_NOW` | Pins the timestamp written to `first_seen` / `last_scanned` / `missing_since`. Exists so tests and evals are deterministic; leave unset in normal use. |
| `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY` | Read by **Litestream**, not by `atlas.py`; the generated config references them by name. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | A failed check or requirement: WAL could not be set, the index is missing, a required capability is absent, a target is under-specified, or the generator would have leaked a secret. |
| 2 | Usage error (argparse, or a missing `--bucket`). |
