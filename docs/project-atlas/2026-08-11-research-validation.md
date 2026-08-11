# base-in-reality validation — project-atlas design research

**Date:** 2026-08-11 · **Scope:** the design claims behind the `project-atlas` skill (not a repo audit) · **Max claims:** 9

Applied per `base-in-reality/SKILL.md`: claims extracted, routed to sources, verified only
against material fetched in this session, and reported under
`base-in-reality/references/verdict-rubric.md`. The grounding invariant holds — every
`VIOLATION`/`DEVIATION` below cites a URL fetched today; anything ungrounded is
`UNCONFIRMED`.

## Protocol deviations (stated up front)

Two departures from the skill's full procedure, recorded rather than glossed:

1. **Refutation was reasoning-pass, not subagent fan-out.** The rubric asks for ≥3
   independent refuters on distinct lenses. This session ran the three lenses
   (claim-correctness, source-applicability, severity) as sequential passes by one agent,
   so the votes are correlated. Severity is calibrated down accordingly: no finding here is
   rated above `high`, and each one's evidence is a primary-source quote rather than an
   inference.
2. **The primary vendor docs are unreachable from this environment.** `litestream.io`,
   `www.sqlite.org`, and `developers.cloudflare.com` are all blocked by the network egress
   proxy (verified: `curl` returns `000`, WebFetch returns `EGRESS_BLOCKED`). Grounding
   therefore comes from the Litestream **source tree and in-repo docs** on
   `raw.githubusercontent.com`, which is the more authoritative artifact anyway for
   version-specific behaviour. Claims that only the blocked sources could settle are
   reported `UNCONFIRMED` — see F7.

## Domain map

- **Detected domain(s):** local developer tooling; SQLite storage engine internals;
  S3-compatible object storage replication.
- **Norms surface in scope:** the Litestream 0.5.x source tree and its in-repo docs
  (`docs/PROVIDER_COMPATIBILITY.md`, `skills/litestream/SKILL.md`), Litestream release
  history, and a direct runtime probe of the local SQLite build.
- The domain is engineering-practice, not scientific — the scholarly source classes in
  `references/source-routing.md` do not apply, so routing went to vendor/primary source.

## Findings

### [medium] DEVIATION — a `replicas:` list is the deprecated Litestream config shape

- **Claim:** the skill's generated config should use `dbs[].replicas[]`, as most Litestream
  tutorials show.
- **Layer:** arch
- **Assessment:** false for the 0.5.x series the skill targets. The config struct marks the
  list form deprecated and the loader rejects more than one entry, so generating a list is
  at best legacy syntax and at worst a hard failure. The singular mapping is the current
  shape. This drove spec requirement **R8**.
- **Citations:**
  - Litestream `cmd/litestream/main.go` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/main.go> —
    "`Replica  *ReplicaConfig   \`yaml:\"replica\"\`` / `Replicas []*ReplicaConfig \`yaml:\"replicas\"\` // Deprecated`"
    and "multiple replicas on a single database are no longer supported"
  - Litestream `etc/litestream.yml` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/etc/litestream.yml> —
    the shipped example uses "`replica:`" under each `dbs:` entry
- **Recommended fix:** emit `replica:` as a mapping; never emit `replicas:`.

### [high] VIOLATION — an R2 endpoint without an explicit `https://` scheme defeats provider detection

- **Claim:** `endpoint=ACCOUNT_ID.r2.cloudflarestorage.com` (the form most blog posts use)
  is sufficient for Cloudflare R2.
- **Layer:** arch
- **Assessment:** insufficient. Litestream applies its R2 preset — signed payloads,
  `concurrency=2`, checksums off — only when it recognises the endpoint, and recognition
  keys on the scheme. Without it, the replica runs with AWS defaults against a backend
  that rejects them (R2 supports neither `aws-chunked` encoding nor request checksums, and
  caps concurrent uploads). This drove **R9**.
- **Citations:**
  - Litestream `docs/PROVIDER_COMPATIBILITY.md` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/docs/PROVIDER_COMPATIBILITY.md> —
    "**Important**: The endpoint must use `https://` scheme for R2 detection to work."; and
    for R2, "Does not support `aws-chunked` content encoding", "Does not support
    request/response checksums", "Strict concurrent upload limit (2-3 concurrent uploads max)"
  - Litestream `s3/replica_client.go` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/s3/replica_client.go> —
    "`// DefaultR2Concurrency is the default number of concurrent multipart upload` / `//
    parts for Cloudflare R2, which has strict concurrent upload limits.` /
    `const DefaultR2Concurrency = 2`"
- **Recommended fix:** always generate the endpoint with the scheme attached and assert it
  in the generator's tests.

### [medium] DEVIATION — inheriting the default sync interval bills an API call per second

- **Claim:** the generated config can omit `sync-interval` and take the default.
- **Layer:** arch
- **Assessment:** the default is one second. For a project index that changes when a human
  runs a scan — not continuously — a 1s cadence is ~86k sync cycles/day against object
  storage for a database that may not have changed at all. Defensible for a busy
  application database, wasteful here; hence `DEVIATION`, not `VIOLATION`. This drove **R10**.
- **Citations:**
  - Litestream `replica.go` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/replica.go> —
    "`// Default replica settings.` / `const (` / `DefaultSyncInterval    = 1 * time.Second`"
- **Recommended fix:** emit an explicit `sync-interval` (the skill defaults to 10s) and
  document raising it.

### [medium] DEVIATION — Litestream requires WAL and takes the database's journal mode into its own hands

- **Claim:** any SQLite file can be handed to Litestream as-is.
- **Layer:** algo
- **Assessment:** Litestream forces `journal_mode = wal` on the database it replicates and
  errors out if the pragma does not take (a rollback-journal database on a read-only
  filesystem, for instance, will not convert). An index created in the default rollback
  mode is therefore not replication-ready until something switches it. Making the indexer
  open WAL itself removes the failure mode instead of discovering it at replication time.
  This drove **R5**.
- **Citations:**
  - Litestream `db.go` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/db.go> —
    "`// Enable WAL and ensure it is set. New mode should be returned on success:` … `if err
    := db.db.QueryRowContext(ctx, \`PRAGMA journal_mode = wal;\`).Scan(&mode); err != nil {`
    … `} else if mode != \"wal\" { return fmt.Errorf(\"enable wal failed, mode=%q\", mode) }`"
- **Recommended fix:** set and verify WAL on every connection the indexer opens; fail loudly
  when the mode does not stick.

### [low] DEVIATION — Litestream writes its own tables into the replicated database

- **Claim:** the index database contains only tables the skill created.
- **Layer:** arch
- **Assessment:** replication adds bookkeeping objects to the user database itself, so any
  code that enumerates tables (a `stats` command, a schema check, a migration) must tolerate
  names it did not create. Low severity — it is a nuisance, not a corruption — but it is the
  kind of assumption that breaks a `SELECT name FROM sqlite_master` loop months later.
- **Citations:**
  - Litestream `db.go` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/db.go> —
    "`// Create a table to force writes to the WAL when empty.` … `CREATE TABLE IF NOT
    EXISTS _litestream_seq (id INTEGER PRIMARY KEY, seq INTEGER);`" and "`// Create a lock
    table to force write locks during sync.`"
- **Recommended fix:** filter `_litestream%` and `sqlite_%` wherever the tooling enumerates
  tables.

### [low] DEVIATION — one database replicates to exactly one destination

- **Claim:** the index can be fanned out to S3 *and* R2 for redundancy from a single `dbs:`
  entry.
- **Layer:** arch
- **Assessment:** not in 0.5.x. Single-replica-per-database is stated as an invariant in
  Litestream's own agent skill and enforced in the config loader. Belt-and-braces
  redundancy has to come from bucket-level replication or a second Litestream process
  against a copy — not from the config. The skill should say so rather than let a user
  discover it.
- **Citations:**
  - Litestream `skills/litestream/SKILL.md` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/skills/litestream/SKILL.md> —
    "### 3. Single Replica per Database — Each database replicates to exactly one
    destination."
  - Litestream `cmd/litestream/main.go` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/main.go> —
    "cannot specify 'replica' and 'replicas' on a database"
- **Recommended fix:** document the constraint in the skill's boundaries section.

### [low] DEVIATION — Litestream's own provider doc uses the syntax its loader deprecates

- **Claim:** copying the config block out of `docs/PROVIDER_COMPATIBILITY.md` yields a
  current-shape config.
- **Layer:** arch
- **Assessment:** it does not. Every provider block in that document is written as
  `replicas:` — the form `main.go` marks `// Deprecated`. This is an upstream doc lag, not a
  code defect, but it explains why nearly every third-party R2 tutorial shows the legacy
  shape, and it is why the skill's generator is tested against the *struct tags* rather than
  against the prose.
- **Citations:**
  - <https://raw.githubusercontent.com/benbjohnson/litestream/main/docs/PROVIDER_COMPATIBILITY.md>
    — "```yaml` / `replicas:` / `  - url: s3://bucket-name/path?endpoint=https://ACCOUNT_ID.r2.cloudflarestorage.com```"
  - <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/main.go>
    — "`Replicas []*ReplicaConfig \`yaml:\"replicas\"\` // Deprecated`"
- **Recommended fix:** none for this repo; ground the generator on the loader, not the docs.

### [low] OUTDATED — "Litestream is 0.3.x / beta" is a stale mental model

- **Claim:** Litestream is at 0.3.x, config-compatible with the widely-circulated 2021-era
  examples.
- **Layer:** arch
- **Assessment:** the released series is 0.5.x, with v0.5.16 dated 2026-08-05 — six days
  before this validation. The 0.5 line changed the on-disk replication format (LTX) and the
  config shape, which is precisely why the 0.3-era tutorials mislead. The skill pins its
  claims to 0.5.x and says so.
- **Citations:**
  - Litestream releases — <https://github.com/benbjohnson/litestream/releases> — v0.5.16
    (2026-08-05), v0.5.15 (2026-07-21), v0.5.14 (2026-07-06)
  - Litestream `skills/litestream/SKILL.md` —
    <https://raw.githubusercontent.com/benbjohnson/litestream/main/skills/litestream/SKILL.md>
    — "monitors the SQLite WAL (Write-Ahead Log), converts changes to immutable LTX files,
    and replicates them to cloud storage"
- **Recommended fix:** state the targeted Litestream series in the skill's compatibility
  line, and validate generated configs against 0.5.x semantics.

### [—] UNCONFIRMED — "FTS5 is compiled into every SQLite build the skill will meet"

- **Claim:** the skill can assume FTS5 and skip a fallback path.
- **Layer:** algo
- **Assessment:** **not grounded.** The authoritative statement lives on `sqlite.org`, which
  is blocked by this environment's egress proxy, so no fetched source settles it. A direct
  probe of *this* machine (python 3.11.15, SQLite 3.45.1) shows `ENABLE_FTS5` present and
  both the default and `trigram` tokenizers usable — but one machine is not the population,
  and distribution builds do vary. Per the grounding rule this stays `UNCONFIRMED` and the
  design absorbs the uncertainty instead of asserting it away: **R6** requires a runtime
  probe and a `LIKE` fallback that preserves the output protocol.
- **Citations:** none fetched (`https://www.sqlite.org/fts5.html` → `EGRESS_BLOCKED`).
  In-session observation only: `PRAGMA compile_options` on the local interpreter returned
  `ENABLE_FTS5`, and `CREATE VIRTUAL TABLE … USING fts5(…)` succeeded.
- **Recommended fix:** keep the probe-and-fallback; re-verify against sqlite.org from an
  unrestricted environment before ever removing it.

## Sources appendix

1. Litestream releases page — benbjohnson/litestream — <https://github.com/benbjohnson/litestream/releases>
2. Litestream `cmd/litestream/main.go` (config structs, loader validation) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/main.go>
3. Litestream `etc/litestream.yml` (shipped example config) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/etc/litestream.yml>
4. Litestream `docs/PROVIDER_COMPATIBILITY.md` — <https://raw.githubusercontent.com/benbjohnson/litestream/main/docs/PROVIDER_COMPATIBILITY.md>
5. Litestream `s3/replica_client.go` — <https://raw.githubusercontent.com/benbjohnson/litestream/main/s3/replica_client.go>
6. Litestream `db.go` — <https://raw.githubusercontent.com/benbjohnson/litestream/main/db.go>
7. Litestream `replica.go` — <https://raw.githubusercontent.com/benbjohnson/litestream/main/replica.go>
8. Litestream `cmd/litestream/restore.go` (restore CLI surface) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/restore.go>
9. Litestream `skills/litestream/SKILL.md` (upstream agent skill) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/skills/litestream/SKILL.md>
10. Litestream `README.md` — <https://raw.githubusercontent.com/benbjohnson/litestream/main/README.md>

Blocked, therefore not cited: `litestream.io/*`, `www.sqlite.org/*`,
`developers.cloudflare.com/r2/*`.

## Dropped-claims log

None — all 9 extracted claims were routed and verified. Two claims the design touches were
deliberately **not** extracted because the user scoped them out: Cloudflare Durable Object
SQLite storage limits, and full-source-tree indexing cost. If either comes back into scope,
they need their own grounding pass — and the Durable Object one cannot be grounded from this
environment at all, since `developers.cloudflare.com` is blocked.
