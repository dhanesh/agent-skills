# ops-intake: operational signals back into planning (roadmap step 5B)

**Status:** The owner approved the design in brainstorming on 2026-10-06, section by section. This spec is for the owner's review.

**Context:** release-conductor (5A, PR #70, merge `11cd42d`) carries a merged change to verified production. After that merge, Jev judged the repo again:
- Q1 (the repo is a software factory) stays **partly 0.91**, because the operate → support/feedback → planning stages are missing.
- Q3 is **yes 0.73**.
- Jev picked step 5B as the next work (0.59).

Today nothing carries what happens in production back into planning.

**Goal:** When the human asks "what broke?", the factory reads operational signals. It dedupes them and ranks them into a local queue. The signals come from:
- release results,
- failing CI on the default branch,
- GitHub issues,
- Jira or Linear issues.

Every source reaches intake through a CLI, an MCP server or a local file. The agent runs the CLI or calls the MCP tool. Intake only parses what the agent gives it. Intake accepts a closed list of formats, and each format has one adapter. When the human picks an item, intake hands it to spec-first-planning as a typed request with quoted evidence. spec-first-planning drafts the spec and plan. The human approves them as today. A verified release that names the item marks it resolved. Intake runs no command and opens no network connection. It holds no tokens. It sends nothing anywhere. It never starts the factory itself.

**Decision support:** Jev (`jev-latest`) gave input on every owner decision. An Opus advisor reviewed the approach and the safety constraints.

## Owner decisions

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| D1 | **Triage and draft.** Intake collects, dedupes and ranks signals. For an item the human picks, it produces the request from which spec-first-planning drafts a spec and plan. The human's plan approval still starts the factory. | 0.79 | Auto-fix narrow classes (0.12); ranked queue only (0.09) |
| D2 | **Sources in v1:** release records, failing CI on the default branch, GitHub issues, and Jira or Linear issues. | 0.84 / 0.76 / 0.76 | Generic recipe adapters (0.38): deferred |
| D3 | **MCP servers and CLIs are the ingestion channels. Intake holds no tokens.** The owner uses the Atlassian MCP server, the Linear MCP server and the Atlassian CLI (`acli`). The CLIs and MCP servers own authentication. Intake accepts only supported formats, and one adapter per format turns source data into the intake shape. (Amended at spec review, 2026-10-06. It replaces the first choice, built-in REST readers with a token from an environment variable, Jev 0.58.) | owner decision | Built-in REST readers with tokens (0.58, chosen first, then replaced) |
| D6 | **The agent runs every CLI and MCP tool.** It pipes the output into `intake import`. Intake never runs a command or opens a connection. So a config edit cannot run code, and the human's permission prompts stay the control. | 0.80 | Intake runs `gh` itself through fixed built-in commands (0.20) |
| D4 | **On demand only in v1.** A scheduled sweep is deferred. Under D3 and D6 a sweep would need an agent to run the CLIs and MCP tools, so it is a later design. | 0.71 | Opt-in sweep in v1 (0.29). An earlier, lopsided framing scored the sweep 1.00, and the owner was told. |
| D5 | **A new `ops-intake` skill.** It hands a picked item to spec-first-planning as an `intake-item/v1` envelope, so spec-first-planning stays the only spec writer. | 1.00 (lopsided; the advisor's reasoning concurs) | An intake mode inside spec-first-planning; extending bug-autopsy |

## 1. Components and data

### 1.1 The skill

`ops-intake/` follows the repo anatomy: `SKILL.md`, `README.md`, `references/`, `assets/`, `eval/`.

The tool is `assets/intake.py`. It is stdlib-only, Python 3.10+, POSIX. Every command takes `--root R` before the command name: `intake.py --root R <command> …`. The commands (ruling R19 corrected this table):

| Command | Effect |
|---|---|
| `init --answers FILE` | Writes `.intake/config.json` from an answers file. The agent interviews the human to get the answers. |
| `import --format FMT --source NAME FILE` | Parses one source output file (or `-` for stdin) with the adapter for `FMT`. It adds the signals to the queue. |
| `sync` | Reads the local release envelopes, applies recurrence and the loop-closing rules (1.6), and ranks the queue. |
| `formats` | Lists the supported formats and the command or MCP tool that produces each one. |
| `list [--all]` | Prints the ranked queue. Without `--all`, it shows only `new` and `regressed` items. |
| `show <id>` | Prints one item and its quoted evidence. |
| `pick <id> --by NAME` | Writes the `intake-item/v1` envelope and marks the item `picked`. |
| `dismiss <id> --by NAME --reason TEXT` | Marks the item `dismissed`. |
| `link <id> <id>` | Merges two items when the human confirms they are the same issue. |
| `resolve <id> --by NAME` | Marks the item `resolved` by hand. |
| `status` | Shows a read-only summary. |

### 1.2 Config

The config is at `.intake/config.json`. It is committed and holds no secrets. It holds only data, never a command:
- **`sources`:** for each source name, its enabled flag and the formats it uses. Example: `{"ci": {"enabled": true, "formats": ["gh-runs-json", "gh-run-jobs-json"]}}`. `import` refuses a source that is not configured and enabled, or a format that source does not list, because the source name is part of the item id. The name `release` is reserved for the two release formats. `git-rev-list` is auxiliary and belongs to no source.
- **`github`:** the repo and the labels that mark a signal (default `bug`, `incident`). The agent uses these values when it runs `gh`.
- **`ci`:** the default branch name and the workflows on it to watch.
- **`jira`:** one JQL query. The agent uses it when it calls the MCP tool or `acli`.
- **`linear`:** a team key or filter.
- **`weights`:** source weights for ranking.

A PR can edit the config. That is safe here, because the config holds no command, no host and no credential name. The worst edit changes which data the agent asks for, and the agent's permission prompts still show every command and MCP call.

### 1.2a Formats and adapters

Intake accepts a closed list of formats. Each format has one strict adapter with the interface `adapt(raw) -> (signals, problems)`.

| Format | Produced by | Status in v1 |
|---|---|---|
| `release-envelope` | release-conductor `release-result/v1` envelopes under `.skill-contract/envelopes/`, read locally by `sync` | built in |
| `release-status` | the text output of release-conductor's `status` command (`RELEASE: <version> <status>` lines), piped in by the agent | built in |
| `gh-issues-json` | `gh issue list --repo OWNER/REPO --state all --limit 100 --json number,title,body,labels,state,createdAt,updatedAt,url` (erratum, final review: `createdAt` is required, `--state all --limit 100` as built) | built in |
| `gh-runs-json` | `gh run list --branch <default> --json databaseId,workflowName,headBranch,headSha,event,status,conclusion,createdAt,updatedAt,url,attempt` | built in |
| `gh-run-jobs-json` | `gh run view <run id> --json jobs`, imported with `--run <run id>` | built in |
| `jira-mcp` | the Atlassian MCP server's `searchJiraIssuesUsingJql` tool | built only when the owner supplies a real sample |
| `jira-acli` | `acli jira workitem search --jql <JQL> --json` | built only when the owner supplies a real sample |
| `linear-mcp` | the Linear MCP server's `list_issues` tool | built only when the owner supplies a real sample |
| `git-rev-list` | `git rev-list <release commit>`, imported with `--commit <release commit>`, for loop closing | built in |
| `intake-signals-jsonl` | the user's own transform, one signal per line in the 1.3 shape | built in; marked lower trust |

Rules:
- **Unknown format:** `import` refuses it (exit 2).
- **Malformed record:** the adapter reports it as a problem and continues with the next record.
- **Provenance:** every signal keeps `source`, `source_id` and `url`.
- **CI jobs:** `gh run list` has no job field. So a failing run becomes an item only after its jobs are imported. `sync` prints, for each failing run without jobs, `NEXT: gh run view <id> --json jobs | intake import --format gh-run-jobs-json --source <name> --run <id>`. `<name>` is the configured source that imported the run (ruling R18). The jobs output has no workflow or branch, so intake joins it to the imported run by run id. A failure is `status` `completed` with `conclusion` `failure`, `timed_out` or `startup_failure`.
- **Default branch:** the `gh` output does not name it, so the `ci` config holds it.
- **Timestamps:** adapters accept RFC 3339 and the `+0000` offset form (no colon).
- **No guessed shapes:** the Jira and Linear adapters are written only from a real, redacted sample. The format research (`2026-10-06-ops-intake-formats.md`) found no documented output for these three. The owner supplies each sample from their own MCP server or `acli`. Until then, Jira and Linear data can come in through `intake-signals-jsonl`. The redacted sample becomes the adapter's test fixture. If a sample is missing, that adapter is not built, and its format stays out of the list.
- **Lower trust:** a signal from `intake-signals-jsonl` shows `trust: low` in the envelope and `+low-trust` in the `ITEM:` flag of `list` and `show` (erratum, final review C1: `list` did not show it before). Its provenance is only what the user's transform claims.

**The pipeline:** source output → format adapter → signal → queue → `pick` → `intake-item/v1`. The `intake-item/v1` envelope is the format that spec-first-planning reads. spec-first-planning reads these fields from it: `item_id`, `title`, `kind`, `severity`, `source`, `source_id`, `url`, `trust`, `count`, `first_seen`, `last_seen` and `evidence`.

### 1.3 Signal shape

The tool normalises the signals from every source to one shape:

```
{source, source_id, url, kind: release|ci|issue, title, severity: 1-4,
 first_seen, last_seen, count, evidence: [{text, source, source_id, fetched_at, key}]}
```

- **Evidence key** (erratum, final review): each evidence entry carries a `key`, and a repeat with a key already held adds no evidence and no count.
- **Dedupe:** the key is `(source, source_id)`. A repeat raises `count` and `last_seen`. It also appends any new evidence, up to a cap per item.
- **What `source_id` is, per source:**
  - **CI:** `workflow/job/branch`, not the run id. So repeated failures of one job are one item. Each failing run becomes evidence and raises `count`.
  - **issue:** the issue key.
  - **release:** the release version.
- **Cross-source links:**
  - A Jira key or Linear id named in a GitHub issue is only a *suggested* link.
  - `link` merges two items after the human confirms.
  - v1 promises no semantic dedupe.
- **Severity mapping, per source:**
  - release `prod_failed`/`outcome_unknown` = 4;
  - `rolled_back` = 3;
  - a failing CI run on the default branch = 3;
  - a GitHub issue maps from fixed label names: `incident` = 4, `bug` = 3, any other = 2. The config cannot change this; `github.labels` only selects which issues the agent asks `gh` for (ruling R19). An `intake-signals-jsonl` record carries its own severity;
  - unknown = 2.

### 1.4 Ranking

Ranking is deterministic code. It sorts by severity, then recency, then frequency, and it weights each of these by source. The same inputs always give the same order.

The agent MAY propose a reordering in conversation. The tool never asks a model. The tool MUST NOT call Jev or any other model, because ranking is policy that belongs in code and ticket text can carry customer data.

### 1.5 Queue and states

The queue is `.skill-contract/intake/queue.json`. It is git-ignored, and the tool writes it by atomic replace. Next to it is an append-only `intake-log.jsonl`.

| From | To | When |
|---|---|---|
| (none) | `new` | `import` (or `sync`, for a rolled-back release envelope) sees a signal for the first time; an issue already closed then makes no item (R27) |
| `new` | `picked` | `pick` |
| `new`, `picked` or `planned` | `dismissed` | `dismiss` (needs a reason; final review B6) |
| `picked` | `planned` | a `task-plan/v1` envelope names the item id |
| `planned` | `resolved` | a verified `release-result/v1` commit contains every proven task's merge commit (1.6); the resolution time is the release's time, and an item seen after it recurs at once (R25) |
| any | `resolved` | `resolve` by hand (logged with the name) |
| `dismissed` or `resolved` | `new` + `regressed` flag | the signal *recurs* after the dismissal or resolution time (defined below) |

**Recurrence is defined per source.** Seeing a still-open issue again is not recurrence. Otherwise every dismissed issue would come back on each sync.
- **issue:** reopened, or updated after the dismissal or resolution time.
- **CI:** a new failing run of that job, started after that time.
- **release:** a new failed release after that time.

A repo-wide lock guards `sync` and every state change. The lock and the atomic writes are copies of release-conductor's code, with a cross-reference comment. Skills cannot import each other, so a copy is necessary.

### 1.6 The handoff

`pick` writes an `intake-item/v1` envelope. The envelope holds the item's fields and its quoted evidence. Its subject is a per-item snapshot file. The envelope and the snapshot go to the git-ignored `.skill-contract/intake/` directory, because the evidence can carry customer data.

spec-first-planning gains one input path: it accepts this envelope as the request. The drafted spec carries the item id. It keeps the evidence in a fenced **"External evidence (untrusted)"** block, apart from the requirements.

The task-plan envelope carries the id in `intake_items` (a list of ids). This is a new optional payload field in `task-plan.v1.json`. The field is optional, so existing plans stay valid.

The loop closes without any change to factory-conductor or release-conductor. Each `sync` does these steps:
1. It finds the `task-plan/v1` envelope that names a `picked` item. It marks the item `planned`.
2. It finds the `run-result/v1` that pins that plan. It records the `merge_commit` of each proven task in that run. These commits come from the envelope, not from the live run branch. That branch is often deleted after merge.
3. It marks the item `resolved` when a `release-result/v1` with outcome `verified` has a commit whose history contains every one of those commits. Intake runs no command, so the agent pipes `git rev-list <release commit>` into `intake import --format git-rev-list`.

A squash or rebase merge breaks that ancestry. The item then stays `planned`, and `list --all` says so (`planned+needs-resolve`; erratum, final review: `list` without `--all` shows only `new` items). A proven run with no verified release after it shows `planned+needs-release`. The human closes the item with `resolve`. v1 accepts this limit and does not guess.

`docs/skill-contract/SPEC.md` registers the `intake-item/v1` kind.

## 2. Trust boundary

Everything the tool reads from a source is untrusted. This includes issue bodies, titles, CI logs and ticket fields. Anyone can file an issue on a public repo. This section follows the two-channel rule of crafting-self-prompting-loops: trusted instructions and untrusted data never share a channel.

1. **External text is data.**
   - The tool stores and shows evidence only as quoted, code-spanned text. Each piece of evidence shows its source, id and fetch time.
   - `list` neutralises titles in the same way as release-conductor's `code()`:
     - one line, code-spanned;
     - no markdown headings;
     - no live `@mentions`;
     - no issue-closing keywords;
     - hidden characters (ESC, bidi overrides, zero-width spaces) written as `\uXXXX` (final review A4).
   - The title is untrusted too. The spec's H1 is `# Spec: intake <item id>`, and the title is a code span inside the evidence section, where the copy tripwire sees it. So the task-plan title carries no raw untrusted text (final review A2).
   - **Customer evidence is never committed by default** (ruling R24). spec-first-planning writes an intake spec to the git-ignored `.skill-contract/intake/specs/<item id>.md`. The final review proved that the plan, the grant and a factory-conductor run work from that path. spec-first-planning tells the human before it commits any file that quotes evidence.
2. **Nothing executable crosses the boundary.**
   - spec-first-planning MUST NOT lift a check command, a path to execute, a URL to fetch, or an install step from evidence. Otherwise an untrusted issue would become code that factory-conductor runs unattended after the human approves the plan.
   - The agent writes every acceptance check from the repo.
   - spec-first-planning warns when any check command contains text copied from the external-evidence block. The warning prints next to that command's `CHECK_COMMAND:` line and names the copied text. The lint still passes, because bug reports often name the failing test file, and an honest check reuses it (owner decision, Jev 0.95 over a hard failure). The test is a shared substring of at least 12 characters, after NFKC normalisation, case folding and whitespace folding on both sides (erratum, final review: stronger than an exact match).
   - This check is a tripwire, not the boundary. The planning agent reads the evidence. So injected text can make it write an attacker's command in its own words. No substring check catches that.
   - **The real boundary is the human's approval.** For a plan that comes from intake, the approval step lists every check command verbatim. The human approves those commands and knows that the request came from untrusted text.
3. **The human's approval is informed.**
   - The plan-approval step names the item. It also shows that the external block is untrusted.
   - Intake never starts the factory.
4. **No credentials, no network, no commands.**
   - Intake holds no token. The CLIs and MCP servers own authentication.
   - Intake MUST NOT open a network connection or run a subprocess, because then a config edit or a hostile record could make it act. Everything it reads comes from a file, stdin or a local envelope.
5. **Untrusted text can reach the agent before intake sees it.**
   - **CLI:** the agent redirects CLI output straight into a file or a pipe for `import`. It does not read the output first. That keeps untrusted text out of the agent's context.
   - **MCP:** an MCP tool result lands in the agent's context before intake can neutralise it. Intake cannot fix this. The SKILL tells the agent to treat that result as data only, and the approval step that lists every check command verbatim stays the backstop.
6. **Read-only.**
   - The agent uses only read and search commands and MCP tools: `gh issue list`, `gh run list`, the MCP search and read tools, and `acli` search.
   - Intake cannot enforce this for MCP calls or CLIs, because the agent makes those calls. The human's permission prompts are the real control. The honesty section of the SKILL says so.
   - Intake never comments, labels, assigns, transitions or closes anything. "Resolved" is local state only.
7. **No data leaves the machine** through intake. The tool sends nothing to Jev or to any other service.

## 3. Flow, states and failures

### 3.1 The agent's flow

When the human asks something like "what broke?":

1. For each enabled source, the agent runs the CLI or calls the MCP tool. It pipes the output into `intake import --format <fmt> --source <name>`. `intake formats` lists the command or tool for each format.
2. `intake sync`, then `intake list`. The agent shows the top items in plain words.
3. The human picks an item, or does nothing. The agent MUST NOT pick or dismiss for the human, because choosing what to work on is the human's decision.
4. `intake pick <id> --by <human>` writes the envelope. It prints `NEXT: run spec-first-planning with <envelope path>`.
5. spec-first-planning drafts the spec and plan from the envelope. The human approves them as today. Then factory-conductor and release-conductor continue.

### 3.2 Failures, per source

- **Per-import result:** each `import` prints `IMPORT: <source> <format> ok <n> problems <m>`.
- **A failed source:** it keeps its last items. It does not block the other sources.
- **A missing source:** if the agent cannot run a CLI or reach an MCP server, it reports that source as skipped to the human. The queue keeps that source's last items.
- **Exit codes:**
  - 0 when the command succeeds;
  - 3 when an import had problem records, a sync met a bad envelope, or the intake lock was held (erratum, final review);
  - 2 for an invalid config, an unknown format, refused input or an unreadable queue.
- **Machine lines:** `IMPORT:`, `SYNC:`, `ITEM: <id> <flag> <rank> <title>`, `NEXT:`, `STOP:`, `PROBLEM:`. The flag is the state plus `+regressed`, a wait such as `+needs-resolve`, and `+low-trust` (erratum, final review).

## 4. Testing, gates, acceptance

### 4.1 Tests

Tests are offline and stdlib-only.

- **No network, no commands:** a test patches `socket` and `subprocess` to raise. Every intake command still works.
- **Adapters:** each format has fixtures:
  - a valid file;
  - a file with one malformed record (the others still import);
  - an unknown format (refused, exit 2).
  The `gh` fixtures come from documented `gh --json` output. The `jira-mcp`, `jira-acli` and `linear-mcp` fixtures are the owner's redacted real samples.
- **Lower trust:** an `intake-signals-jsonl` signal shows `+low-trust` in `list` and `trust: low` in the envelope.
- **Release:** the tests use real `release-result/v1` envelopes. The tests build them with the vendored checker.
- **Hostile text:** fixtures with:
  - a markdown heading;
  - an `@mention`;
  - "Closes #1";
  - a backtick fence break;
  - an issue body carrying `curl … | sh`.

  Tests assert two things. First, these appear only as quoted evidence in `list`, `show` and the envelope. Second, a check command that copies them gets a `WARNING:` line next to its `CHECK_COMMAND:` line (the copy tripwire, owner decision in §2.2), and a check command with a hidden or control character fails spec-first-planning's lint. (Ruling R16 corrected this sentence, which said the linter fails a copied command.)
- **Other properties:**
  - the order is deterministic;
  - the lock holds;
  - the state machine follows 1.5, including `regressed` on recurrence;
  - each source's failure is isolated.

### 4.2 Eval

`eval/run_eval.py` runs in under 60 s on Ubuntu.

The positive case: fixture files for every supported format go through `import`, `sync` and `pick` to an envelope that spec-first-planning accepts. A verified `release-result/v1` then resolves the item.

The negatives:
- intake opens no socket and runs no subprocess;
- an unknown format is refused;
- a check command that copies hostile evidence carries the `WARNING:` line next to its `CHECK_COMMAND:` line, and a hidden character in a check command fails the lint (ruling R16);
- a dismissed item stays dismissed unless it recurs;
- a malformed record does not drop the other records.

### 4.3 Gates

- `make gate` is green.
- ops-intake is a skill-contract adopter. It consumes `release-result/v1`, `run-result/v1` and `task-plan/v1`. It provides `intake-item/v1`.
- BCP 14 register rows exist for the new SKILL.md.
- Every absolute states its reason (PP-5).
- The README catalog lists the skill. The Software factory section gains the "operate → planning" stage.
- Mutation-proven A/B rows exist for each guard, with a new `SINCE_*` constant.

### 4.4 Acceptance criteria

| AC | Done when |
|---|---|
| AC1 | Every supported format imports through its adapter from a fixture. An unknown format is refused. The Jira and Linear adapters are deferred until the owner supplies redacted real samples (Task 7); until then that data comes in through `intake-signals-jsonl` (erratum, final review). |
| AC2 | The trust boundary holds in three places: intake keeps external text as quoted evidence; a check command copied from it carries a warning next to its line; the approval step lists every check command verbatim. |
| AC3 | The loop closes: a picked item becomes a plan that names it, and a verified release containing that plan's run marks it resolved; squash merges are reported as needing a manual `resolve`. |
| AC4 | Intake opens no socket and runs no subprocess, proven by a test that makes both raise. The config holds no command, host or credential name. |
| AC5 | The eval passes, including every negative. |
| AC6 | `make gate` and `make ab-validate` are green: 0 WORSE, 0 UNPROVEN. |
| AC7 | After merge, Jev re-judges Q1 against the merge commit, and the score is recorded in the factory assessment. |

## 5. Out of scope for v1

- the scheduled sweep;
- generic recipe adapters (Sentry, uptime, logs, support inbox);
- semantic cross-source dedupe;
- any write-back to a tracker;
- intake starting the factory by itself;
- business-loop analytics (roadmap step 6).
