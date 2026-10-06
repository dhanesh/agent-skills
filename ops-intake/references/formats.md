# ops-intake formats, config and machine lines

This file is the reference for `intake.py`. It lists only the formats that are built. The
research behind each format, with its sources, is in the repository at
`docs/superpowers/specs/2026-10-06-ops-intake-formats.md`.

All text that a source gives is untrusted data. Intake stores it as quoted evidence and shows it
only as one-line code spans.

## The closed list of formats

`intake formats` prints one `FORMAT: <name> <help>` line for each format. Any other name gets
`STOP: unknown-format` (exit 2).

| Format | Produced by | Source name | Gives |
|---|---|---|---|
| `release-envelope` | release-conductor `release-result/v1` envelopes under `.skill-contract/envelopes/` | `release` | an item for a `rolled_back` release |
| `release-status` | release-conductor's `release status` (`RELEASE: <version> <status>` lines) | `release` | an item for a failed release |
| `gh-issues-json` | `gh issue list --repo OWNER/REPO --state all --limit 100 --json number,title,body,labels,state,createdAt,updatedAt,url` | any configured name | an item per issue |
| `gh-runs-json` | `gh run list --branch <default> --json databaseId,workflowName,headBranch,headSha,event,status,conclusion,createdAt,updatedAt,url,attempt` | any configured name | failing runs, held until their jobs come in |
| `gh-run-jobs-json` | `gh run view <run id> --json jobs`, imported with `--run <run id>` | any configured name | an item per failing job |
| `git-rev-list` | `git rev-list <release commit>`, imported with `--commit <release commit>` | none (no `--source`) | the release history, for loop closing |
| `intake-signals-jsonl` | your own transform, one signal per line | any configured name | an item per line, `trust: low` |

`jira-mcp`, `jira-acli` and `linear-mcp` are not built. Their output is not documented. Each
adapter waits for a redacted real sample from the owner. Until then, send Jira and Linear data
through `intake-signals-jsonl`.

### release-envelope

- `sync` reads it. Do not import it: `import --format release-envelope` stops with exit 2.
- Sync reads it only when the `release` source is enabled and lists `release-envelope`.
  Without that, a rolled-back release gives no item. Verified releases still close the loop.
- A `rolled_back` envelope gives one item: kind `release`, severity 3, `source_id` = the
  version. A `verified` envelope gives no item.
- Sync accepts an envelope only when it passes the skill-contract checks and names
  `release-conductor` as its producer.

### release-status

- One `RELEASE: <version> <status>` line per release. `RELEASE: none` and blank lines are
  skipped. Any other line is a problem.
- Only a semantic version gives an item. Archived names, such as `1.2.0.abandoned-…`, are
  skipped.
- Severity: `prod_failed` 4, `outcome_unknown` 4, `rolled_back` 3, `stage_failed` 3,
  `abandoned` 2. Other statuses (`verified`, `staged` and so on) are skipped.
- The text has no times. So the same version seen again never counts as a recurrence.

### gh-issues-json

- One JSON array. Each record needs `number` (a positive integer), `title`, `url`, `state`
  (`OPEN` or `CLOSED`), `createdAt` and `updatedAt`. `body` can be a string or null. `labels`
  is a list of objects with a `name`.
- `source_id` = the issue number. `first_seen` = `createdAt`. `last_seen` = `updatedAt`.
  The evidence is the body.
- Severity comes from fixed label names: `incident` gives 4, `bug` gives 3, anything else 2.
  `github.labels` in the config only tells you which labels to ask `gh` for. It does not change
  the severity.
- A `CLOSED` issue is recorded as closed. A close never makes a dismissed item recur.

### gh-runs-json and gh-run-jobs-json

- `gh-runs-json` keeps a run only when `headBranch` equals `ci.default_branch`, the workflow is
  in `ci.workflows` (when that list is set), `status` is `completed` and `conclusion` is
  `failure`, `timed_out` or `startup_failure`. It gives no item yet, because `gh run list` has
  no job field.
- Sync then prints, for each failing run whose jobs are not imported:
  `NEXT: gh run view <id> --json jobs | intake import --format gh-run-jobs-json --source <name> --run <id>`.
  `<name>` is the source that imported the run, when it lists `gh-run-jobs-json`. If it does
  not, `<name>` is the first source, by name, that lists it. Run the line as printed.
- `gh-run-jobs-json` needs `--run`, and that run must be imported first (`run-not-imported`
  otherwise). Each failing job gives an item: kind `ci`, severity 3,
  `source_id` = `<workflow>/<job>/<branch>`. Repeated failures of one job are one item; each
  run adds evidence (the failed step names) and raises `count`.
- A complete, well-formed jobs payload marks the run done, and its `NEXT:` line goes away.
- An unknown `status` or `conclusion` value is a problem for that record only.

### git-rev-list

- One 40-hex sha per line, as `git rev-list` prints them (newest first). The first line must
  be the `--commit` value. If it is not, nothing is stored.
- Empty output means the commit is not in this clone. Sync then prints, once,
  `NEXT: git fetch, then re-import: …` and the item waits at `needs-resolve`.
- At most 20000 shas are kept per commit. Histories no planned item needs are dropped.
- A sha256 repository (64-hex commits) cannot use this format. Its items wait at
  `needs-resolve`.

### intake-signals-jsonl

One JSON object per line:

```json
{"source": "jira", "source_id": "OPS-1", "url": "https://jira.example/browse/OPS-1",
 "kind": "issue", "title": "db slow", "severity": 3,
 "first_seen": "2026-10-01T00:00:00Z", "last_seen": "2026-10-02T00:00:00Z",
 "evidence": [{"text": "p99 up", "key": "a"}]}
```

- `source` must equal `--source`. `kind` is `release`, `ci` or `issue`. `severity` is 1 to 4.
- `evidence` is optional. A missing `key` becomes a hash of the text.
- `trust` is always `low`. A `trust` field in the record is ignored.
- Times: RFC 3339 with `Z`, `+HH:MM` or `+HHMM` (Jira's form), with or without fractions.

### Rules for every format

- A malformed record is a `PROBLEM:` line that names its position and the field. The other
  records still import. The import then exits 3.
- Problem lines never quote the source's text.
- Caps: 5000 signals per import, 500 characters per title, 4000 per evidence text, 20 evidence
  entries per item (the oldest goes first).
- Dedupe key: `(source, source_id)`. The item id is `I` + 10 hex of its sha256.
- Recurrence: a dismissed or resolved item becomes `new` with `regressed` when a signal for it
  has a `last_seen` later than the time it was closed.

## Config: `.intake/config.json`

Write it with `intake init --answers <file>`. It is committed. It holds only data.

```json
{
  "sources": {
    "github":  {"enabled": true, "formats": ["gh-issues-json"]},
    "ci":      {"enabled": true, "formats": ["gh-runs-json", "gh-run-jobs-json"]},
    "release": {"enabled": true, "formats": ["release-envelope", "release-status"]},
    "jira":    {"enabled": true, "formats": ["intake-signals-jsonl"]}
  },
  "github": {"repo": "owner/name", "labels": ["bug", "incident"]},
  "ci": {"default_branch": "main", "workflows": ["gate"]},
  "jira": {"jql": "project = OPS AND statusCategory != Done"},
  "weights": {"release": 1.5, "ci": 1.2}
}
```

- Allowed top-level keys: `sources`, `github`, `ci`, `jira`, `linear`, `weights`. Any other key
  is refused. So are `command`, `base_url` and `*_env` inside a section.
- A source name is 1 to 50 letters, digits, `_`, `.` or `-`, and starts with a letter or digit.
  The name goes into `NEXT:` lines that you run in a shell, so other characters are refused.
- Each source has exactly `enabled` (true or false) and `formats` (a non-empty list).
- The name `release` is only for `release-envelope` and `release-status`, and those two
  formats are only allowed there.
- `git-rev-list` is auxiliary. No source can list it.
- `ci.default_branch` is required when a source lists a CI format.
- `github.repo` looks like `owner/name`. `jira.jql`, `linear.team` and `linear.filter` are
  short strings. You use them when you run the CLI or MCP tool; intake does not.
- `weights` maps configured source names to numbers. The default is 1.

## Ranking

`rank = (severity × 100 + recency + min(count, 20)) × weight`. `recency` is 30 minus the days
since `last_seen`, never below 0. Sync stores the rank. `list` sorts by rank, highest first,
and breaks ties by item id. No model takes part.

## States

| From | To | When |
|---|---|---|
| (none) | `new` | the first signal for the item |
| `new` | `picked` | `pick --by` |
| `new` | `dismissed` | `dismiss --by --reason` |
| `picked` | `planned` | a task-plan from spec-first-planning, made at or after the pick, lists the item in `intake_items` |
| `planned` | `resolved` | the latest run-result pinning the plan has every task `proven` with a merge commit, and a verified release made at or after it has every merge commit in its imported history |
| any but `resolved` | `resolved` | `resolve --by` (by hand) |
| `dismissed` or `resolved` | `new` + `regressed` | the signal recurs |

The newest plan that names the item wins. A revision (`wasRevisionOf`) of the item's plan that
drops it flags `plan-superseded`.

## Commands and machine lines

| Command | Prints |
|---|---|
| `init --answers <file>` | `NEXT: config written to .intake/config.json` |
| `import --format F [--source S] [--run ID] [--commit SHA] [FILE]` | `IMPORT: <source> <format> ok <n> problems <m>`, `PROBLEM:` lines (at most 50, then `PROBLEM: and <k> more`) |
| `sync` | `SYNC: <n> items new=… picked=… planned=… resolved=… dismissed=… envelopes <e> problems <p>`, `PROBLEM:` and `NEXT:` lines |
| `list [--all]` | `ITEM: <id> <flag> <rank> <title>` per item (`new` only, without `--all`) |
| `show <id>` | the `ITEM:` line, `source:` and one `evidence:` line per entry |
| `pick <id> --by NAME` | `ITEM:` and `NEXT: run spec-first-planning with <envelope path>` |
| `dismiss <id> --by NAME --reason TEXT`, `resolve <id> --by NAME` | `ITEM:` |
| `link <keep> <other>` | `NEXT: merged <other> into <keep>` |
| `status` | `SYNC: <n> items … regressed=<r>` |
| `formats` | `FORMAT:` lines (needs no config) |

The flag is `<state>`, plus `+regressed`, plus for `picked` and `planned` items one of
`+needs-plan`, `+needs-history`, `+needs-resolve` or `+plan-superseded`.

Exits: 0 OK; 3 problem records, a bad envelope, or the intake lock held for 900 s; 2 invalid
config, unknown format, refused input or an unreadable queue (`STOP:` line, nothing changed).

## Files

| Path | Committed | What |
|---|---|---|
| `.intake/config.json` | yes | the config |
| `.skill-contract/intake/queue.json` | no (git-ignored) | the queue, written by atomic replace |
| `.skill-contract/intake/intake-log.jsonl` | no | an append-only log of every change |
| `.skill-contract/intake/items/<id>.json` | no | the snapshot that `pick` pins |
| `.skill-contract/intake/envelopes/<id>.json` | no | the `intake-item/v1` envelope |

The intake directory writes its own `.gitignore`, because evidence can carry customer data.

## The planner side (spec-first-planning)

- `intake_request.py <envelope>` prints the start of a spec: the title, `## Intake` with the
  item id, and `## External evidence (untrusted)`. That section has a note that it is data, one
  metadata line, and one fenced block per evidence entry. A fence is always longer than any
  backtick run in the text, so the text cannot close it.
- `spec_lint.py` fails a `[cmd: …]` that holds a hidden or control character (for example
  bidi controls, zero-width characters, ANSI escapes or NUL). It also fails `## Intake` without
  the evidence section, and the reverse.
- `spec_to_tasks.py` prints `INTAKE: <ids>` and one `CHECK_COMMAND: <task> <argv>` line per
  verify command. A command that shares 12 or more characters with the evidence section (after
  case folding and NFKC) gets `WARNING: <task> copies untrusted evidence: <text>` right after
  its line. This is a warning, not a failure: a bug report often names the failing test, and an
  honest check can reuse that name.
- The evidence section includes the metadata line. So a command that repeats the item's
  `source_id` or URL (for example a CI `workflow/job/branch`) also gets a warning. Read each
  warning. Do not dismiss them by habit.
- The task-plan payload carries `intake_items`. That is how sync finds the plan for an item.
