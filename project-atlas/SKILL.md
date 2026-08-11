---
name: project-atlas
description: >-
  Index every project on a local machine into one durable SQLite database — path, git remote,
  branch, HEAD, languages, toolchains — and capture agent sessions (chat transcripts plus the
  task state a resume needs) into the same database, all full-text searchable, then replicate
  it to S3, Cloudflare R2, Backblaze B2, or any S3-compatible bucket with Litestream 0.5.x.
  Use when the user says "index all my repos", "catalogue the projects on this machine",
  "where is that project that...", "back up / sync my project index to S3 or R2", "save my
  Claude Code chats", "search my past sessions", or "resume this session on my other
  machine". Restores a captured session onto a different machine, rewriting paths so it can
  be resumed there. Ships a stdlib-only scanner, session capture with credential redaction on
  by default, an FTS5-or-LIKE search CLI, a capability doctor, and a Litestream config
  generator. Not a code-search engine over whole source trees, not a background daemon, not a
  credential or account sync, and not a Cloudflare Durable Object deployment.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell; git optional (enriches metadata); the Litestream binary is needed only to replicate or restore, never to scan, capture or search. Session capture reads Claude Code's agent home ($CLAUDE_CONFIG_DIR or ~/.claude). Offline except for replication itself.
metadata:
  author: dhanesh
  version: "1.1.0"
  tags: "sqlite,litestream,fts5,indexing,search,replication,s3,r2,backup,local-projects"
---

# project-atlas

Turn a machine full of half-remembered checkouts — and the conversations that shaped them —
into one queryable store that survives the machine. The index is a single SQLite file;
search is FTS5 over project docs and captured chats alike; durability is Litestream
streaming that file to object storage, with a restore command generated alongside the
config. Restore it elsewhere and you get both halves back: where the work lives, and the
session you were in the middle of.

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
- **A captured session** — one row per (agent, session id, host): cwd, git branch, CLI
  version, turn counts, the sha256 of the original transcript, plus every transcript record
  stored verbatim in order and the session's task state. Credential-shaped strings are
  redacted before storage; the agent's account config is never read at all.
- **Output protocol** — every command ends in one machine-readable line:
  `SCAN_RESULT:`, `SEARCH_RESULT:`, `STATS_RESULT:`, `DOCTOR_RESULT:`, `SESSIONS_RESULT:`,
  `RESTORE_RESULT:`. Most commands also take `--json`. Exit 0 on success, 1 on a failed
  check or requirement, 2 on a usage error.

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
5. **Capture the chats, if the user wants sessions to travel too.**
   `atlas.py sessions ingest` (add `--limit N` to take only recent ones). Redaction is on
   by default and the `SESSIONS_RESULT:` line reports what it removed — relay that count,
   because it is the user's evidence that scrubbing happened. `--no-redact` exists and
   prints a warning; only reach for it if the user asks for byte-exact transcripts and
   controls the bucket. Transcripts are large (a single active session ran to 1.8 MB), so
   say what capturing everything will cost before doing it.
6. **Make it durable.** `atlas.py litestream-config --target r2|s3|b2|custom|file …
   --out <path>` writes the config and prints the runbook: the `litestream replicate`
   command to run, and the `litestream restore` commands (latest, point-in-time, dry-run)
   for recovery. Bring up replication, then **verify the restore before trusting it** — a
   replica nobody has restored from is a hypothesis. Restore to a scratch path and open it:
   `litestream restore -o /tmp/atlas-check.db "<url>" && python3 assets/atlas.py stats --db /tmp/atlas-check.db`.
7. **Resume elsewhere, when that is the goal.** On the other machine: restore the index
   from the replica, `atlas.py sessions search` for the work, then
   `atlas.py sessions restore <id> --cwd <path-on-this-machine>`. The `--cwd` flag is the
   one that matters — it recomputes the agent's project directory and rewrites every
   record's path, which is what makes a Linux capture resumable on a Mac. The command
   prints the `claude --resume` line to run. `references/sessions.md` has the refusals and
   what each verification level actually proves.
8. **Keep it fresh.** Neither the scanner nor the capture is a daemon. Schedule them — a
   cron entry running `atlas.py scan <roots>` and then `atlas.py sessions ingest --limit 50`
   daily is usually right. Litestream is the only long-running piece;
   `references/replication.md` has the service-unit shape.

## Deliverable

A **populated index plus a working replica**: `SCAN_RESULT: OK` with the project count,
a `search` that returns the user's own projects, a written Litestream config that passes
the constraints in `references/replication.md`, and a restore command the user has actually
run once. Report the index path, project count, replica target, and the engine (`fts5` or
`like`) in your summary — those four facts are what the user needs to trust the thing.

## Verify

Before handing back, run the skill's own checks:

```sh
python3 -m unittest discover -s assets -p 'test_*.py'   # 96 unit checks
python3 eval/run_eval.py                                # end-to-end outcome eval
```

The eval builds a synthetic machine *and* a synthetic agent home, indexes both, deletes a
project, captures a session, and relocates it onto a different machine's layout — then
grades the result, including negative fixtures that must be rejected (a deprecated
`replicas:` config, a bare R2 endpoint, an inline credential, a `Makefile`-only directory,
a planted credential that must not reach the database, account identity that must never be
read, a redacted capture that must refuse to restore, a broken parent chain). It ends in
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
  write a config containing a live credential value from the environment. Session capture
  redacts credential-shaped strings by default and never reads the agent's account config.
- **Session formats are undocumented, so nothing here parses them.** Records are stored
  verbatim and only the envelope is read. A restore is verified, not assumed — but the
  proof stops at the artifact: that the CLI accepts a restored session and continues it is
  confirmed by your first real resume, not by the offline eval.
- **Not a live handoff.** Resume means restore-then-continue after a sync, not two machines
  writing one conversation at once.

## References

- `references/parameters.md` — every subcommand, flag, exit code, and environment variable.
- `references/schema.md` — table definitions, the FTS design, and SQL recipes for the
  questions the CLI does not answer directly (stale checkouts, duplicate remotes, …).
- `references/replication.md` — Litestream 0.5.x specifics, per-provider settings, the
  restore drill, scheduling, and the grounded citations behind each constraint.
- `references/sessions.md` — the agent-session layout, what is deliberately never captured,
  redaction, and the cross-machine resume procedure with its three refusals.

## Assets

- `assets/atlas.py` — the scanner, session commands, search CLI, doctor, and config
  generator (stdlib only).
- `assets/sessions.py` — session discovery, redaction, transcript round-tripping, restore
  path mapping.
- `assets/test_atlas.py`, `assets/test_sessions.py` — the unit suites.
- `eval/run_eval.py` — the outcome eval described under **Verify**.
