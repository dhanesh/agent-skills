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

When the human picks an item, intake hands it to spec-first-planning as a typed request with quoted evidence. spec-first-planning drafts the spec and plan. The human approves them as today. A verified release that names the item marks it resolved. Intake only reads from each source. It sends nothing anywhere. It never starts the factory itself.

**Decision support:** Jev (`jev-latest`) gave input on every owner decision. An Opus advisor reviewed the approach and the safety constraints.

## Owner decisions

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| D1 | **Triage and draft.** Intake collects, dedupes and ranks signals. For an item the human picks, it produces the request from which spec-first-planning drafts a spec and plan. The human's plan approval still starts the factory. | 0.79 | Auto-fix narrow classes (0.12); ranked queue only (0.09) |
| D2 | **Sources in v1:** release records, failing CI on the default branch, GitHub issues, and Jira or Linear issues. | 0.84 / 0.76 / 0.76 | Generic recipe adapters (0.38): deferred |
| D3 | **Jira and Linear through built-in REST readers.** The tool calls Jira REST and Linear GraphQL with `urllib`. The token comes from an environment variable named in the config. | 0.58 | Generic JSON import (0.24); vendor CLIs (0.11); the agent's MCP servers (0.07) |
| D4 | **On demand only in v1.** A scheduled sweep is deferred until token storage at timer time is settled. | 0.71 | Opt-in sweep in v1 (0.29). An earlier, lopsided framing scored the sweep 1.00, and the owner was told. |
| D5 | **A new `ops-intake` skill.** It hands a picked item to spec-first-planning as an `intake-item/v1` envelope, so spec-first-planning stays the only spec writer. | 1.00 (lopsided; the advisor's reasoning concurs) | An intake mode inside spec-first-planning; extending bug-autopsy |

## 1. Components and data

### 1.1 The skill

`ops-intake/` follows the repo anatomy: `SKILL.md`, `README.md`, `references/`, `assets/`, `eval/`.

The tool is `assets/intake.py`. It is stdlib-only, Python 3.10+, POSIX. Its commands:

| Command | Effect |
|---|---|
| `init --root R` | Writes `.intake/config.json` from an answers file. The agent interviews the human to get the answers. |
| `confirm-config --root R --by NAME` | Records the config digest that the human has read and accepts. |
| `sync --root R` | Reads every enabled source and updates the queue. |
| `list --root R [--all]` | Prints the ranked queue. Without `--all`, it shows only `new` and `regressed` items. |
| `show --root R <id>` | Prints one item and its quoted evidence. |
| `pick --root R <id> --by NAME` | Writes the `intake-item/v1` envelope and marks the item `picked`. |
| `dismiss --root R <id> --by NAME --reason TEXT` | Marks the item `dismissed`. |
| `link --root R <id> <id>` | Merges two items when the human confirms they are the same issue. |
| `resolve --root R <id> --by NAME` | Marks the item `resolved` by hand. |
| `status --root R` | Shows a read-only summary. |

### 1.2 Config

The config is at `.intake/config.json`. It is committed and holds no secrets. The config enables or disables each source.

- **`release`:** reads `release-result/v1` envelopes under `.skill-contract/`. It also reads release state that ended `prod_failed`, `outcome_unknown`, `rolled_back`, `stage_failed` or `abandoned`. Where an envelope exists, it reads the envelope kind, not the internal state files of release-conductor. For a release that ended without an envelope, it reads the output of the documented `status` command.
- **`ci`:** the workflows on the default branch to watch. The tool reads this source with `gh run list`.
- **`github`:** the repo and the labels that mark a signal (default `bug`, `incident`). The tool reads this source with `gh issue list`.
- **`jira`:**
  - the base URL;
  - one JQL query;
  - the *names* of the environment variables that hold the account email and the API token;
  - `max_items`.
- **`linear`:**
  - a team key or filter;
  - the *name* of the environment variable for the API key;
  - `max_items`.
- **`weights`:** source weights for ranking.

**The config is pinned, because a PR can edit it.** The config is committed, and it names a host and an environment variable. That makes it an exfiltration path. A PR could point `base_url` at an attacker's host. Or it could name `AWS_SECRET_ACCESS_KEY` as the "token". The owner's next `sync` would then send that secret. So:
- **Hosts:**
  - Linear's host is hardcoded (`api.linear.app`).
  - A Jira base URL must match `https://<name>.atlassian.net`. The only exception is a host that is also listed in `.intake/hosts.local`. That local file is untracked, and a PR cannot change it.
- **Environment variable names:** they must start with `INTAKE_`. The tool refuses any other name.
- **Confirmation:**
  - `sync` refuses (`STOP: config-changed`, exit 3) when the config's sha256 differs from the confirmed digest.
  - The confirmed digest is the one the human last confirmed with `intake confirm-config --by NAME`.
  - The tool stores that digest in the git-ignored queue directory. This is the same pinning idea as release-conductor's recipe.
- **Tests:** each rule has a test.

### 1.3 Signal shape

The tool normalises the signals from every source to one shape:

```
{source, source_id, url, kind: release|ci|issue, title, severity: 1-4,
 first_seen, last_seen, count, evidence: [{text, source, source_id, fetched_at}]}
```

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
  - issues map from their priority or label (the defaults are documented, and the config can override them);
  - unknown = 2.

### 1.4 Ranking

Ranking is deterministic code. It sorts by severity, then recency, then frequency, and it weights each of these by source. The same inputs always give the same order.

The agent MAY propose a reordering in conversation. The tool never asks a model. The tool MUST NOT call Jev or any other model, because ranking is policy that belongs in code and ticket text can carry customer data.

### 1.5 Queue and states

The queue is `.skill-contract/intake/queue.json`. It is git-ignored, and the tool writes it by atomic replace. Next to it is an append-only `intake-log.jsonl`.

| From | To | When |
|---|---|---|
| (none) | `new` | `sync` sees a signal for the first time |
| `new` | `picked` | `pick` |
| `new` | `dismissed` | `dismiss` (needs a reason) |
| `picked` | `planned` | a `task-plan/v1` envelope names the item id |
| `planned` | `resolved` | a verified `release-result/v1` commit contains every proven task's merge commit (1.6) |
| any | `resolved` | `resolve` by hand (logged with the name) |
| `dismissed` or `resolved` | `new` + `regressed` flag | the signal *recurs* after the dismissal or resolution time (defined below) |

**Recurrence is defined per source.** Seeing a still-open issue again is not recurrence. Otherwise every dismissed issue would come back on each sync.
- **issue:** reopened, or updated after the dismissal or resolution time.
- **CI:** a new failing run of that job, started after that time.
- **release:** a new failed release after that time.

A repo-wide lock guards `sync` and every state change. The lock and the atomic writes are copies of release-conductor's code, with a cross-reference comment. Skills cannot import each other, so a copy is necessary.

### 1.6 The handoff

`pick` writes an `intake-item/v1` envelope. The envelope holds the item's fields and its quoted evidence, with the item id as a subject.

spec-first-planning gains one input path: it accepts this envelope as the request. The drafted spec carries the item id. It keeps the evidence in a fenced **"External evidence (untrusted)"** block, apart from the requirements.

The task-plan envelope carries the id in `intake_items` (a list of ids). This is a new optional payload field in `task-plan.v1.json`. The field is optional, so existing plans stay valid.

The loop closes without any change to factory-conductor or release-conductor. Each `sync` does these steps:
1. It finds the `task-plan/v1` envelope that names a `picked` item. It marks the item `planned`.
2. It finds the `run-result/v1` that pins that plan. It records the `merge_commit` of each proven task in that run. These commits come from the envelope, not from the live run branch. That branch is often deleted after merge.
3. It marks the item `resolved` when a `release-result/v1` with outcome `verified` has a commit that contains every one of those commits (`git merge-base --is-ancestor`).

A squash or rebase merge breaks that ancestry. The item then stays `planned`, and `list` says so. The human closes the item with `resolve`. v1 accepts this limit and does not guess.

`docs/skill-contract/SPEC.md` registers the `intake-item/v1` kind.

## 2. Trust boundary and credentials

Everything the tool reads from a source is untrusted. This includes issue bodies, titles, CI logs and ticket fields. Anyone can file an issue on a public repo. This section follows the two-channel rule of crafting-self-prompting-loops: trusted instructions and untrusted data never share a channel.

1. **External text is data.**
   - The tool stores and shows evidence only as quoted, code-spanned text. Each piece of evidence shows its source, id and fetch time.
   - `list` neutralises titles in the same way as release-conductor's `code()`:
     - one line, code-spanned;
     - no markdown headings;
     - no live `@mentions`;
     - no issue-closing keywords.
2. **Nothing executable crosses the boundary.**
   - spec-first-planning MUST NOT lift a check command, a path to execute, a URL to fetch, or an install step from evidence. Otherwise an untrusted issue would become code that factory-conductor runs unattended after the human approves the plan.
   - The agent writes every acceptance check from the repo.
   - spec-first-planning's linter fails a spec when any check command contains text copied from the external-evidence block. The test is an exact substring of at least 12 characters.
   - This check is a tripwire, not the boundary. The planning agent reads the evidence. So injected text can make it write an attacker's command in its own words. No substring check catches that.
   - **The real boundary is the human's approval.** For a plan that comes from intake, the approval step lists every check command verbatim. The human approves those commands and knows that the request came from untrusted text.
3. **The human's approval is informed.**
   - The plan-approval step names the item. It also shows that the external block is untrusted.
   - Intake never starts the factory.
4. **Credentials.**
   - The tool reads the Jira credentials (email and API token) and the Linear API key from environment variables that the config names. Their values MUST NOT be written to the config, the queue, the log or any envelope. The reason: agents read those files, and some of the files are committed.
   - If a variable is missing, the result is `SYNC: <source> skipped no-credentials`. This is not an error.
   - Requests go only to the configured base URL, over HTTPS, with a timeout. They paginate up to `max_items`.
5. **Read-only.**
   - `gh` runs only read verbs: `run list`, `issue list`, and `api` with GET.
   - Jira calls are GET only.
   - Linear calls are GraphQL `query` documents. The tool refuses to send any document that contains a `mutation` operation. The reason: writing to a tracker is an external message, and that class is never grantable.
   - Intake never comments, labels, assigns, transitions or closes anything. "Resolved" is local state only.
6. **No data leaves the machine,** other than the source reads themselves. The tool sends nothing to Jev or to any other service.

## 3. Flow, states and failures

### 3.1 The agent's flow

When the human asks something like "what broke?":

1. `intake sync`, then `intake list`. The agent shows the top items in plain words.
2. The human picks an item, or does nothing. The agent MUST NOT pick or dismiss for the human, because choosing what to work on is the human's decision.
3. `intake pick <id> --by <human>` writes the envelope. It prints `NEXT: run spec-first-planning with <envelope path>`.
4. spec-first-planning drafts the spec and plan from the envelope. The human approves them as today. Then factory-conductor and release-conductor continue.

### 3.2 Failures, per source

- **Per-source result:** each source prints `SYNC: <source> ok <n> | skipped <why> | failed <why>`.
- **A failed source:** it keeps its last items. It does not block the other sources.
- **No `gh`:** if `gh` is missing or not logged in, `ci` and `github` report `skipped`.
- **Exit codes:**
  - 0 when every enabled source is ok;
  - 3 when some were skipped or failed;
  - 2 for an invalid config or refused input.
- **Machine lines:** `SYNC:`, `ITEM: <id> <state> <rank> <title>`, `NEXT:`, `STOP:`.

## 4. Testing, gates, acceptance

### 4.1 Tests

Tests are offline and stdlib-only.

- **`gh`:** a stub `gh` on PATH serves canned JSON. Any write verb or non-GET `api` call fails the test.
- **Jira and Linear:**
  - A local `http.server` covers pagination, 401, timeouts and malformed JSON.
  - A test asserts that the tool refuses a `mutation` document before it sends any request.
- **Release:** the tests use real `release-result/v1` envelopes that the vendored checker builds.
- **Hostile text:** fixtures with:
  - a markdown heading;
  - an `@mention`;
  - "Closes #1";
  - a backtick fence break;
  - an issue body carrying `curl … | sh`.

  Tests assert two things. First, these appear only as quoted evidence in `list`, `show` and the envelope. Second, spec-first-planning's linter fails a spec whose check command contains them.
- **Credentials:** a planted fake token is absent from every file that intake writes.
- **Other properties:**
  - the order is deterministic;
  - the lock holds;
  - the state machine follows 1.5, including `regressed` on recurrence;
  - each source's failure is isolated.

### 4.2 Eval

`eval/run_eval.py` runs in under 60 s on Ubuntu.

The positive case: four stubbed sources go through `sync` and `pick` to an envelope that spec-first-planning accepts. A verified `release-result/v1` then resolves the item.

The negatives:
- no write verb or mutation is ever sent;
- no token reaches disk;
- hostile text never reaches a check command;
- a dismissed item stays dismissed unless it recurs;
- a failed source does not drop the others.

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
| AC1 | All four sources are read through read-only calls, proven against stubs that fail on any write. |
| AC2 | The trust boundary holds in three places: intake keeps external text as quoted evidence; spec-first-planning's linter refuses check commands copied from it (a tripwire); the approval step lists every check command verbatim. |
| AC3 | The loop closes: a picked item becomes a plan that names it, and a verified release containing that plan's run marks it resolved; squash merges are reported as needing a manual `resolve`. |
| AC4 | No credential value is ever written to disk, logs or envelopes. A config edit cannot send a credential elsewhere: the host and env-name rules hold, and sync refuses an unconfirmed config. |
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
