# Agent sessions: capture, search, and resuming elsewhere

The index stores project *metadata* and, through these commands, the *conversations* that
produced the work — transcript plus the side state a resume needs. Replicate the index and
the chats travel with it; restore it on another machine and a half-finished session can be
picked back up.

Every claim about the on-disk layout below was verified by direct observation, not from
documentation — the vendor docs for it are not reachable, and these are internal formats
with no stability guarantee. The consequences of that are baked into the design; see
`docs/project-atlas/2026-08-11-research-validation.md` for the full evidence record.

## What is on disk

```
~/.claude/projects/<slug>/<session-id>.jsonl   the transcript, one JSON object per line
~/.claude/tasks/<session-id>/<n>.json          the task list
~/.claude/sessions/<pid>.json                  the live-session descriptor
~/.claude/session-env/<session-id>/            per-session environment
~/.claude/shell-snapshots/*.sh                 serialized shell state
~/.claude.json                                 ACCOUNT config — never captured
```

`<slug>` is the working directory with `/` replaced by `-`, so `/home/dev/payments`
becomes `-home-dev-payments`. Override the agent home with `--home` or
`$CLAUDE_CONFIG_DIR`.

A transcript record carries an envelope — `type`, `uuid`, `parentUuid`, `timestamp`,
`sessionId`, `cwd`, `gitBranch`, `version`, `message`, and often `toolUseResult` — and the
set of `type` values is open (`user`, `assistant`, `attachment`, `last-prompt`,
`queue-operation`, `system` were all observed). Records are stored **verbatim**; only the
envelope is interpreted. A record type this tool has never seen is captured in full and
replayed unchanged.

## Capture

```sh
python3 assets/atlas.py sessions ingest                    # every session in the agent home
python3 assets/atlas.py sessions ingest --limit 20         # the 20 most recent
python3 assets/atlas.py sessions ingest --session <id>     # one, repeatable
python3 assets/atlas.py sessions ingest --dry-run          # list, write nothing
```

Capture is idempotent, and it **replaces** a session's events rather than appending: a
transcript can be rewound upstream, and an append would leave two histories mixed in one
row set.

## What is deliberately not captured

- **`~/.claude.json`.** Its top-level keys include `oauthAccount`, `userID`, and
  `machineID`. That is account identity, it is not needed to resume a conversation, and it
  has no business in a replicated bucket. It is on a never-read list, not merely skipped.
- **Shell snapshots**, unless `--include-shell-snapshots`. A snapshot is a serialized shell
  environment — hundreds of KB, and the likeliest place for an exported credential.
- **Another session's descriptor.** `sessions/<pid>.json` files are matched on `sessionId`.

## Redaction

On by default. Before anything is stored, transcripts and side state are scrubbed for
credential-shaped strings: AWS key ids, GitHub tokens and PATs, Slack tokens, Stripe keys,
Google API keys, PEM private-key blocks, JWTs, bearer headers, credentials embedded in
URLs, and `KEY=value` / `"key": "value"` assignments whose key names a secret. Each match
becomes `[REDACTED:<kind>]`, and the per-kind counts are recorded on the session row.

This matters more than it might seem. Roughly a third of the records in a real transcript
carried a `toolUseResult` — the captured output of commands and file reads. That is how a
`printenv`, a `cat .env`, or a config dump ends up in a chat log verbatim. Without
scrubbing, replication turns each of those moments into a durable remote copy of a live
credential.

```sh
python3 assets/atlas.py sessions ingest --no-redact    # prints a warning; sets redacted=0
```

Opt out when you need byte-exact transcripts and control the destination bucket. The row
records `redacted = 0` so the choice stays visible, and `doctor` reports the count.

The redactor is conservative about shape, not volume — it would rather blank a
harmless-looking token than let a key through. It can therefore alter a conversation that
was *about* a credential pattern; a real capture showed 58 replacements across 456 records,
all in assignment-shaped strings.

## Search

```sh
python3 assets/atlas.py sessions list
python3 assets/atlas.py sessions search "stripe AND webhook"
python3 assets/atlas.py sessions search kafka --json
python3 assets/atlas.py sessions show <id-prefix>
python3 assets/atlas.py sessions show <id> --transcript > session.jsonl
```

Same engine contract as project search: FTS5 where the build has it, a substring scan
otherwise, and `engine=` in the result line tells you which ran. `show` accepts an
unambiguous id prefix; an exact id always wins over a prefix.

## Resuming on another machine

```sh
# 1. on the new machine, restore the index from the replica
litestream restore -o ~/.local/share/project-atlas/atlas.db "<replica-url>"

# 2. find the session
python3 assets/atlas.py sessions search "the thing I was doing"

# 3. write it back into this machine's agent home
python3 assets/atlas.py sessions restore <session-id> --cwd ~/work/payments --allow-redacted

# 4. pick it up (the restore prints this line for you)
claude --resume <session-id>
```

`--cwd` is the important flag. The destination path is usually not the source path — a
Linux `/home/dev/x` becomes a macOS `/Users/dev/x` — so restore recomputes the `projects/`
slug from the target directory and rewrites the `cwd` field of every record. Omit it and
the session is restored where it came from.

### The three refusals

Restore stops rather than producing something that looks fine and is not:

| Refusal | Why | Override |
|---|---|---|
| broken parent chain | a resume walks `parentUuid`; a missing link yields a truncated history that still looks complete | none — repair the capture |
| target file exists | overwriting a live transcript loses whatever the local session did | `--force` |
| capture was redacted | the bytes differ from the original by design, so the transcript is not the one the CLI wrote | `--allow-redacted` |

### What verification actually proves

The restore reports which check it achieved:

- `verify=sha256-exact` — the reconstructed file matches the stored sha256 of the original
  bytes. Available for unredacted captures restored to their original path. This was
  measured on a real 456-record, 1.8 MB transcript: byte-identical.
- `verify=chain-intact` — the bytes changed by design (redaction, or a cwd rewrite), so
  the structural property a resume depends on is checked instead: every non-root
  `parentUuid` resolves to a `uuid` present in the capture.

**What none of this proves:** that the CLI accepts the restored file and continues the
conversation. That needs a second machine and a live CLI, which the offline eval does not
have. The eval proves the artifact is reconstructed correctly; treat the first real resume
as the confirming test, and do it before you rely on this.

## Operating notes

- **Size.** Transcripts are the bulk of the index once sessions are captured — a single
  active session was 1.8 MB. Hundreds of sessions mean hundreds of MB replicated. Use
  `--limit` to capture only recent sessions, and raise `sync-interval` accordingly.
- **Scheduling.** Capture is not a daemon either. A cron entry after the project scan is
  the usual arrangement:
  `17 4 * * * python3 atlas.py scan ~/src && python3 atlas.py sessions ingest --limit 50`
- **One writer.** The same rule as the rest of the index: two machines must not replicate
  to the same bucket path. Give each host its own prefix; the `host` column records which
  machine a capture came from, and the uniqueness key is `(agent, session_id, host)`.
- **A live session captures fine.** The transcript is append-only while a session runs, so
  a capture takes a consistent prefix. Re-ingest later to pick up the rest.
