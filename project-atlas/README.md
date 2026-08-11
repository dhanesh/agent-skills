# project-atlas

An Agent Skill that turns every project directory on a machine — and every agent session
you ran in them — into one durable, searchable SQLite database, replicated to S3,
Cloudflare R2, Backblaze B2, or a local disk with
[Litestream](https://github.com/benbjohnson/litestream), restore included.

The problem it solves: a developer machine ends up with hundreds of checkouts scattered
across `~/src`, `~/work`, and forgotten one-off clones, plus a pile of agent conversations
that only exist in `~/.claude`. "Where is that repo with the Stripe webhook?", "what did I
decide in that session last week?", and "can I pick this up on my laptop?" all become
guesswork — and the answers die with the machine.

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

# capture the chats too (redaction on by default)
python3 assets/atlas.py sessions ingest --limit 50
python3 assets/atlas.py sessions search "stripe webhook"

# make it survive the machine
python3 assets/atlas.py litestream-config --target r2 \
    --bucket atlas-index --account-id $CF_ACCOUNT_ID \
    --out ~/.config/litestream/atlas.yml
litestream replicate -config ~/.config/litestream/atlas.yml
```

The config generator also prints the `litestream restore` commands for the replica it just
configured — latest, point-in-time, and dry-run.

## Resuming a session somewhere else

```sh
litestream restore -o ~/.local/share/project-atlas/atlas.db "<replica-url>"
python3 assets/atlas.py sessions search "the thing I was in the middle of"
python3 assets/atlas.py sessions restore <id> --cwd ~/work/payments --allow-redacted
claude --resume <id>
```

`--cwd` is the flag that makes this work across machines: it recomputes the agent's
project directory from the target path and rewrites every record's `cwd`, so a session
captured on Linux restores onto a Mac. Restore refuses rather than half-working — on a
broken parent chain, on an existing file without `--force`, and on a redacted capture
without `--allow-redacted` — and reports whether it verified `sha256-exact` (byte-identical
to the original) or `chain-intact`.

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

## What gets captured from a session

The transcript, stored record-by-record **verbatim**, plus the session's task state, its
cwd, git branch, CLI version, turn counts, and the sha256 of the original bytes. Chats are
full-text searchable alongside projects.

**Never captured:** `~/.claude.json` — it holds `oauthAccount`, `userID`, and `machineID`,
which is account identity, not session state. Shell snapshots are excluded too unless you
ask for them, since a snapshot is a serialized environment and the likeliest place for an
exported credential.

**Redacted by default.** Around a third of the records in a real transcript carry
`toolUseResult` — captured command output and file reads. That is how a `printenv` or a
`cat .env` ends up in a chat log verbatim, and replicating it unscrubbed would put a live
credential in a bucket. Credential-shaped strings become `[REDACTED:<kind>]` and the counts
are recorded. `--no-redact` exists, warns, and marks the row.

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
- **Session formats are undocumented, so nothing parses them.** Records are stored verbatim
  and only the envelope is read, because a normalized schema would silently drop whatever
  the next CLI version adds. The round trip is *measured*, not assumed: an unredacted
  restore was verified byte-identical against a real 456-record, 1.8 MB transcript.

Each of these traces to a source fetched and quoted in
[`docs/project-atlas/2026-08-11-research-validation.md`](../docs/project-atlas/2026-08-11-research-validation.md),
which also records the one claim that could **not** be grounded (whether FTS5 is present in
every SQLite build) and why the code probes for it instead of assuming.

## Not this

Not a code-search engine over whole source trees — it indexes metadata, paths, and docs; use
ripgrep against the paths it gives you. Not a daemon or file-system watcher — scans and
captures are invoked, and only Litestream runs continuously. Not a Cloudflare Durable
Object / Worker deployment. Not a secrets store, and not an account or credential sync.
Not a live multi-machine handoff either: resume means restore-then-continue after a sync,
not two machines writing one conversation at once.

One boundary worth stating plainly: the offline eval proves the *artifact* is rebuilt
correctly — byte-identical where that is possible, chain-intact and correctly relocated
otherwise. It does not prove the CLI accepts a restored session, because that needs a
second machine and a live CLI. Treat your first real resume as the confirming test.

## Layout

```
project-atlas/
  SKILL.md                  agent-facing prompt
  README.md                 this file
  assets/atlas.py           scanner, session commands, search, doctor, config generator
  assets/sessions.py        session discovery, redaction, transcript round-trip, restore
  assets/test_atlas.py      unit suite (CLI + durability + security guarantees)
  assets/test_sessions.py   unit suite (redaction, parsing, path mapping)
  eval/run_eval.py          end-to-end outcome eval (44 checks, negative fixtures included)
  references/parameters.md  every flag, exit code, environment variable
  references/schema.md      table definitions and SQL recipes
  references/replication.md Litestream specifics, per-provider settings, the restore drill
  references/sessions.md    session layout, what is never captured, cross-machine resume
```

## Development

```sh
make gate-skill SKILL=project-atlas       # structure, frontmatter, leaks, playbook, tests, eval
python3 -m unittest discover -s project-atlas/assets -p 'test_*.py'
python3 project-atlas/eval/run_eval.py
```

The spec and task plan this skill was built from are in
[`docs/project-atlas/`](../docs/project-atlas/).
