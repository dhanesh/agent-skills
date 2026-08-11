---
name: project-atlas
description: >-
  Index every project on a local machine into one durable SQLite database — path, git remote,
  branch, HEAD, languages, toolchains, plus full-text search over READMEs and docs — and
  replicate that database to S3, Cloudflare R2, Backblaze B2, or any S3-compatible bucket with
  Litestream 0.5.x, restore included. Use when the user says "index all my repos", "catalogue
  the projects on this machine", "where is that project that...", "find my checkout of X",
  "back up / sync my project index to S3 or R2", or "make my local projects searchable and
  survive a reinstall". Ships a stdlib-only scanner, an FTS5-or-LIKE search CLI, a capability
  doctor, and a config generator whose output is pinned to Litestream's current config
  grammar. Not a code-search engine over whole source trees, not a background daemon, and not
  a Cloudflare Durable Object deployment.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell; git optional (enriches metadata); the Litestream binary is needed only to replicate or restore, never to scan or search. Offline except for replication itself.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "sqlite,litestream,fts5,indexing,search,replication,s3,r2,backup,local-projects"
---

# project-atlas

Turn a machine full of half-remembered checkouts into one queryable inventory that survives
the machine. The index is a single SQLite file; search is FTS5 over each project's README,
docs, and path; durability is Litestream streaming that file to object storage, with a
restore command generated alongside the config.

The engineering claims underneath — what Litestream's current config grammar is, why an R2
endpoint needs its scheme, why the index must be WAL — were verified against the Litestream
source tree, and the one claim that could not be verified is handled by degrading instead of
asserting. `references/replication.md` carries the citations.

## The contract

- **The index** — one SQLite file (default `$ATLAS_DB`, else
  `$XDG_DATA_HOME/project-atlas/atlas.db`), always in WAL mode. One row per project root:
  path, name, git remote/branch/HEAD/HEAD-time, languages, toolchains, file count, size,
  `first_seen`, `last_scanned`, `missing_since`. A project that disappears is tombstoned,
  never deleted — where something *used to be* is half the value of an inventory.
- **A project root** is a directory holding `.git` or a toolchain manifest
  (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, …). A `Makefile` or
  `Dockerfile` alone is recorded as a toolchain but does not make a directory a project.
  Vendor and build trees (`node_modules`, `.venv`, `target`, `vendor`, …) are never walked.
- **The replica** — a Litestream `replica:` mapping (0.5.x singular form) pointing at S3,
  R2, B2, a custom S3-compatible endpoint, or a local directory, with an explicit
  `sync-interval` and credentials as `${ENV}` expansions only.
- **Output protocol** — every command ends in one machine-readable line:
  `SCAN_RESULT:`, `SEARCH_RESULT:`, `STATS_RESULT:`, `DOCTOR_RESULT:`. `search`, `stats`,
  and `doctor` also take `--json`. Exit 0 on success, 1 on a failed check or requirement,
  2 on a usage error.

Every flag is in `references/parameters.md`; the schema and query recipes are in
`references/schema.md`.

## Workflow

1. **Probe before promising.** Run `python3 assets/atlas.py doctor`. It reports FTS5
   availability, the index's journal mode, and whether `litestream` and `git` are on PATH.
   FTS5 missing is not fatal — search degrades to a substring scan — but say so rather than
   promising ranked search the machine cannot do. Add `--require fts5,litestream` when the
   user's goal actually depends on those.
2. **Agree the roots, then dry-run.** Ask which directories to index (`~/src`, `~/work`,
   `~/Developer` are typical; scanning all of `$HOME` also sweeps up caches and app data).
   Run `atlas.py scan --dry-run <roots>` first and show the count — a dry run that returns
   400 "projects" means the roots are too broad or `--depth` is too high, and that is much
   cheaper to learn before the first index than after.
3. **Index.** `python3 assets/atlas.py scan <roots>`. Re-running is safe and idempotent:
   rows are keyed on path, `first_seen` is preserved, and only `last_scanned` moves. Report
   the `SCAN_RESULT:` line as-is — it carries the indexed/new/missing counts and the search
   engine actually in use.
4. **Search, and check the engine.** `atlas.py search "<terms>"` — FTS5 syntax when
   available (`stripe AND webhook`, `"exact phrase"`, `pay*`). If `SEARCH_RESULT:` reports
   `engine=like`, the query was a substring match: tell the user, because a multi-term FTS
   query means something different to a LIKE scan. `--json` when another tool consumes it.
5. **Make it durable.** `atlas.py litestream-config --target r2|s3|b2|custom|file …
   --out <path>` writes the config and prints the runbook: the `litestream replicate`
   command to run, and the `litestream restore` commands (latest, point-in-time, dry-run)
   for recovery. Bring up replication, then **verify the restore before trusting it** — a
   replica nobody has restored from is a hypothesis. Restore to a scratch path and open it:
   `litestream restore -o /tmp/atlas-check.db "<url>" && python3 assets/atlas.py stats --db /tmp/atlas-check.db`.
6. **Keep it fresh.** The scanner is not a daemon. Schedule it — a cron entry or a launchd
   job running `atlas.py scan <roots>` daily is usually right. Litestream is the only
   long-running piece; `references/replication.md` has the service-unit shape.

## Deliverable

A **populated index plus a working replica**: `SCAN_RESULT: OK` with the project count,
a `search` that returns the user's own projects, a written Litestream config that passes
the constraints in `references/replication.md`, and a restore command the user has actually
run once. Report the index path, project count, replica target, and the engine (`fts5` or
`like`) in your summary — those four facts are what the user needs to trust the thing.

## Verify

Before handing back, run the skill's own checks:

```sh
python3 -m unittest discover -s assets -p 'test_*.py'   # 47 unit checks
python3 eval/run_eval.py                                # end-to-end outcome eval
```

The eval builds a synthetic machine, indexes it, deletes a project, and grades the result —
including negative fixtures that must be rejected (a deprecated `replicas:` config, a bare
R2 endpoint, an inline credential, a `Makefile`-only directory). It ends in
`EVAL_RESULT: PASS (n/n checks)`. If you changed anything under `assets/`, a green eval is
the evidence; a claim without it is a guess.

## Boundaries

- **Metadata and docs, not source.** The searchable document is path, name, README, and
  top-level docs, capped at 64 KiB per project. For grepping code, use ripgrep against the
  paths this index gives you — that is the intended division of labour.
- **One replica per database.** Litestream 0.5.x replicates each database to exactly one
  destination. Belt-and-braces redundancy belongs at the bucket level, not in the config.
- **Not a daemon, not a watcher.** Scans happen when invoked. Only Litestream runs
  continuously.
- **The index is not authoritative about the world.** It is a snapshot with timestamps;
  `missing_since` and `last_scanned` are there so staleness is visible rather than assumed
  away.
- **Secrets stay in the environment.** The generator emits `${…}` expansions and refuses to
  write a config containing a live credential value from the environment.

## References

- `references/parameters.md` — every subcommand, flag, exit code, and environment variable.
- `references/schema.md` — table definitions, the FTS design, and SQL recipes for the
  questions the CLI does not answer directly (stale checkouts, duplicate remotes, …).
- `references/replication.md` — Litestream 0.5.x specifics, per-provider settings, the
  restore drill, scheduling, and the grounded citations behind each constraint.

## Assets

- `assets/atlas.py` — the scanner, search CLI, doctor, and config generator (stdlib only).
- `assets/test_atlas.py` — the unit suite.
- `eval/run_eval.py` — the outcome eval described under **Verify**.
