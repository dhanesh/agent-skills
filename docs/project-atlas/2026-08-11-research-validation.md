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

---

# Addendum — session capture (same day, second scope pass)

The skill's scope grew to cover storing chats and the rest of a session's state so a
session can be resumed elsewhere after a sync. That is a different evidence problem from
the Litestream half: there is no source tree to read and no reachable vendor
documentation, so this pass is grounded in **direct observation of a real agent home on
this machine**, and says so.

## Evidence status, stated plainly

`code.claude.com/docs/...` returns 404 through this environment's proxy, and no other
authoritative description of Claude Code's on-disk session layout was reachable. Under
`references/verdict-rubric.md` that means **no finding below may be reported as a
`VIOLATION`** — the grounding bar is a fetched source and there is none. What exists
instead is primary observation: the files were read, their shapes recorded, and the
round-trip property was *measured* rather than argued. Observations are labelled
`OBSERVED`; the inferences drawn from them about future behaviour are `UNCONFIRMED`.

The distinction matters for one reason above all: an undocumented format carries no
stability promise, so the design must not depend on understanding it.

## What was observed

A live agent home, `/root/.claude`, with one active session:

| Path | Contents |
|---|---|
| `projects/<slug>/<session-id>.jsonl` | the transcript — 456 records, 1.8 MB, one JSON object per line |
| `tasks/<session-id>/<n>.json` | the task list, one file per task |
| `session-env/<session-id>/` | per-session environment directory |
| `sessions/<pid>.json` | live descriptor: `pid`, `sessionId`, `cwd`, `startedAt`, `version`, `entrypoint` |
| `shell-snapshots/*.sh` | a 233 KB serialized shell environment |
| `~/.claude.json` | `oauthAccount`, `userID`, `machineID`, `projects`, cached feature flags |

Record envelope fields present across the transcript: `type`, `uuid`, `parentUuid`,
`timestamp`, `sessionId`, `cwd`, `gitBranch`, `version`, `message`, `toolUseResult`,
`isSidechain`, `userType`, `entrypoint`, `permissionMode`, `requestId`, `effort`.
Observed `type` values: `user`, `assistant`, `attachment`, `last-prompt`,
`queue-operation`, `system`.

## Findings

### [high] OBSERVED — transcripts carry tool output, so they carry secrets

- **Claim:** a chat transcript is conversational text and is safe to replicate as-is.
- **Assessment:** false. 101 of 326 records in the observed transcript carried a
  `toolUseResult` field — the captured output of commands and file reads. That is the
  channel through which an `.env` dump, a `printenv`, or a config file lands in the
  transcript verbatim. Replicating transcripts to object storage without scrubbing turns
  every such moment into a durable, remote copy of a live credential. This is the single
  highest-severity property of the whole session feature, and it drove **R20**: redaction
  on by default, a per-kind count of what was removed, and an explicit warned opt-out.
- **Evidence:** direct read of
  `/root/.claude/projects/-home-user-agent-skills/<session-id>.jsonl`; field census over
  all 326 records at time of inspection.
- **Recommended fix:** redact by default; treat `--no-redact` as a deliberate act.

### [high] OBSERVED — the account config is identity, not session state

- **Claim:** syncing "the session" means syncing `~/.claude` wholesale.
- **Assessment:** it must not. `~/.claude.json` was read and its top-level keys include
  `oauthAccount`, `userID`, and `machineID`. None of that is needed to resume a
  conversation, and all of it is account identity that would then exist in a bucket.
  Drove **R21**: the file is on a never-read list, and shell snapshots — a serialized
  environment, 233 KB in the observed home — are excluded unless explicitly requested.
- **Evidence:** direct read of `/root/.claude.json` (keys only; values not recorded here).
- **Recommended fix:** enumerate what is captured; never capture a home directory wholesale.

### [medium] UNCONFIRMED — the on-disk format is undocumented and version-coupled

- **Claim:** the transcript schema can be parsed into a normalized model and rebuilt.
- **Assessment:** **not grounded, and deliberately not relied upon.** The vendor docs are
  unreachable, the observed records carry a `version` field (`2.1.227` here), and the
  record-type set is open — `queue-operation` and `last-prompt` are not things a naive
  chat model would predict. A tool that parses this into its own schema will silently
  drop whatever the next CLI version adds. Hence **R17**: store every record verbatim as
  its own row, interpret only the envelope fields needed to order and locate a session,
  and record the CLI version alongside.
- **Evidence:** none fetched. Observation only: `https://code.claude.com/docs/en/claude-code/cli-reference` → HTTP 404.
- **Recommended fix:** keep the verbatim-storage rule; re-check against real docs if they
  become reachable.

### [medium] OBSERVED — the project-directory slug is lossy and must not be reused

- **Claim:** the captured `projects/<slug>` directory name can be replayed as-is on the
  destination machine.
- **Assessment:** wrong whenever the path differs, which is the normal case for the
  feature (a Linux `/home/dev/x` becoming a macOS `/Users/dev/x`). The observed mapping is
  the working directory with `/` replaced by `-`: `/home/user/agent-skills` ↔
  `-home-user-agent-skills`, confirmed against the live directory. The transform is not
  injective — a path containing a real `-` collides — so it cannot be inverted to recover
  a cwd. Drove **R22**: the slug is recomputed from the *target* cwd, and the true cwd is
  read from inside the records rather than from the directory name.
- **Evidence:** `/root/.claude/projects/-home-user-agent-skills/` beside a `cwd` field of
  `/home/user/agent-skills` in every record of that transcript.
- **Recommended fix:** recompute; never invert.

### [low] OBSERVED — a transcript is a parent-linked chain, not a flat log

- **Claim:** ordering the records by timestamp is sufficient to reconstruct a session.
- **Assessment:** insufficient in general. 295 of 326 observed records carried a
  `parentUuid`, and the format has an `isSidechain` flag for subagent branches. A resume
  walks that chain, so a capture missing a link yields a truncated history that still
  *looks* complete. Drove **R23**: restore verifies that every non-root `parentUuid`
  resolves to a `uuid` present in the capture, and refuses otherwise.
- **Evidence:** field census over the observed transcript.
- **Recommended fix:** verify the chain at restore time, not at capture time — corruption
  can happen in storage.

### [—] VERIFIED PROPERTY — the round trip is byte-identical

Not a defect; a measurement, recorded because the design leans on it. Parsing the observed
456-record, 1.8 MB transcript and re-serializing it with `json.dumps(...,
ensure_ascii=False, separators=(",", ":"))` reproduced the original file's sha256 exactly.
That is what makes **R18** enforceable: an unredacted restore is checked against the stored
hash rather than trusted. The property is contingent on the writer's serialization
conventions and could break with a CLI change — which is precisely why it is checked at
restore time and reported (`verify=sha256-exact` vs `verify=chain-intact`) instead of
assumed.

## Sources appendix (addendum)

No new fetched sources — the relevant vendor documentation was unreachable. Evidence for
this pass is direct filesystem observation on the machine running the session, recorded
above. Attempted and unavailable: `https://code.claude.com/docs/en/claude-code/cli-reference`
(HTTP 404).

## Dropped-claims log (addendum)

Two claims were extracted and deliberately **not** verified, because verifying them needs a
second machine and a live CLI, which this environment does not have:

1. That `claude --resume <id>` accepts a restored transcript and continues the conversation.
   The skill's eval proves the *artifact* is reconstructed correctly (byte-identical, chain
   intact, correct slug, rewritten cwd); it does not prove the CLI accepts it. This is the
   same manual-protocol boundary `docs/eval-standard.md` draws for model-in-the-loop
   claims, and the skill's documentation states it rather than implying a guarantee.
2. That redaction never removes something a resume needed. The redactor targets
   credential-shaped strings inside message text; a conversation *about* a credential
   pattern could be altered. Observed rate on the live transcript: 58 replacements across
   456 records, all in assignment-shaped strings.
