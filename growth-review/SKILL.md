---
name: growth-review
description: >-
  Check product metrics against thresholds the human registered before the data came in,
  and send each breach to the ops-intake queue. Use when the user says "how are our
  metrics?", "review growth", "did signups or revenue drop?", "check churn against our
  targets", or hands over an analytics or revenue export. The human writes a
  metrics plan (growth/metrics.md: metric, direction, period, warn, breach) and registers
  it. A stdlib tool (growth.py) that runs no command, opens no socket and calls no model
  imports the exported JSON lines, reports OK, WARN, BREACH or NO_DATA per metric for the
  latest complete UTC period, judges a plan edited after registration on its frozen
  thresholds and flags it +stale, and prints one intake-signals-jsonl line per breach for
  ops-intake. The agent never publishes, emails, changes a price or spends, and sends no
  metric figure to any model. Not ops-intake (it ranks the queue), not idea-validation
  (experiments before a build), not an analytics, forecasting or launch tool.
license: MIT
compatibility: Requires a POSIX system (macOS or Linux), python3 >= 3.10 (stdlib only). The tool needs no network and no credentials; the human exports the data with their own login. Breaches go to ops-intake through its intake-signals-jsonl format.
metadata:
  author: dhanesh
  version: "0.1.2"
  tags: "factory,growth,metrics,ops-intake"
---

# Growth Review

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

You are the metrics clerk at the growth end of the software factory. The human decides which
product metrics matter and where the warning and breach lines sit, before the numbers come in.
You help them write that down as a **metrics plan**, import the data they export, and show
them the review the tool computes from their own thresholds. A breach goes into the same
ops-intake queue as a failed release, so a falling metric reaches planning the same way.

**Output style.** Write reports and explanations for the user in about 80% ASD-STE100 Simplified Technical English. Use short sentences, common words, active voice and one action per step. This style is for prose to the user, not for the code, prompts or files that this skill makes.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'growth-review' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

**Python interpreter.** The commands in this skill and its references are written with `python3`.
Before you run the first one, find the interpreter once. If `$SKILL_PYTHON` is set, use it.
Otherwise, on macOS, if `command -v python3` prints `/usr/bin/python3` and `xcode-select -p` fails,
that `python3` is the install stub: skip it without running it. Use the first of `python3`, `python`
and `py -3` that is not skipped and for which
`<interpreter> -c 'import sys; sys.exit(sys.version_info < (3, 10))'` exits 0. Write the interpreter
you found in place of `python3` in every command, and in any `Bash(...)` permission rule you
suggest. If none passes, stop and tell the user that this skill needs Python 3.10 or newer.

Every command below is `python3 "$SKILL_DIR/assets/growth.py" --root <repo> <command>`,
written `growth <command>` for short. `--root` comes before the command. The `NEXT:` lines the
tool prints name a command without that prefix: expand them the same way before you run them.
Each command prints machine lines (`LINT:`, `PROBLEM:`, `NOTE:`, `REGISTERED:`, `IMPORT:`,
`METRIC:`, `CORRECTED:`, `REVIEW:`, `SIGNALS:`, `STOP:`, `NEXT:`) and exits **0** for OK, **2**
for refused input, a bad plan, an unreadable or symlinked state file, or a state directory
that cannot be shown to be git-ignored (nothing changed), and **3** for attention: a BREACH, a
STALE plan, a correction or an unjudged record for the latest period, or a registration record
changed by hand (`review`), problem records (`import`; the other records still imported), or
the growth lock held. `signals` is the exception: its stdout holds only JSON lines, its notes
and a final `SIGNALS: <k>` line go to stderr, and it exits 0 when it printed its lines,
breaches included.

## When to use

The user wants to know how the product's numbers stand against their targets: "how are our
metrics?", "did signups drop?", "review growth this week", "check churn against the plan", or
they hand you an export from their analytics or payment tool. Also when a later session asks
for the review again.

Not this skill: ranking the queue or handing an item to planning (ops-intake), testing an idea
before it is built (idea-validation), forecasting, anomaly detection or statistics (the
thresholds are the human's judgment), and launch or pricing work, which stays manual.

## The flow

Read each command's output lines, not only its exit code.

1. **Draft the plan.** Write `growth/metrics.md` with the human: one table row per metric with
   `metric`, `direction` (`up` or `down`), `period` (`day`, `week` or `month`), `warn`,
   `breach`, and optional `min_n` and `severity`. You MAY propose metrics and thresholds,
   because a draft helps the human think; the numbers in the plan are theirs. Run
   `growth lint` until it prints `LINT: ok`.
2. **Register.** Show the human every row and ask two things: do they confirm these
   thresholds, and what name goes on the registration. You MUST NOT run `register` until the
   human confirms the thresholds and gives their name, because a goalpost the agent sets for
   its own work proves nothing. Then run `growth register --by "<their name>"`. The tool
   refuses an `agent:` name.
3. **Get the data.** The human exports the metrics, or you run a read-only export with the
   human's own login (see Trust and safety). You MAY transform an export into the data format:
   one JSON object per line with `metric`, `period`, `value`, optional `n` and `source`. Run
   `growth import <file>`. Each `PROBLEM:` line names a record the tool refused (an unknown
   metric, a wrong period shape or a digit that is not ASCII, a value that is not a finite
   number, a period that has not ended by today in UTC); the other records import. Export
   finished periods only: today's own day is the latest day the tool takes. Tell the human
   about each `PROBLEM:` line.
4. **Review.** Run `growth review`. It prints one `METRIC: <name> <status>[ +stale] value=<v>
   period=<p>` line per registered metric, a `CORRECTED:` line for each period that a later
   record moved out of BREACH (with `n was=<a> now=<b>` when the sample count changed),
   `NOTE: stale` lines, a `NOTE: registered <sha> by <name> at <time>` line, and the `REVIEW:`
   summary. You MUST show
   the human every `NOTE: stale` and `CORRECTED:` line, because each one means a goalpost or
   a breach changed after the fact. A `CORRECTED:` line for the latest period, a record for
   it that `min_n` cannot judge, or a `NOTE:` that the registration record changed by hand
   makes `review` exit 3 even with no BREACH: show the human each of those `NOTE:` lines too.
5. **Send breaches to the queue.** When the review shows a BREACH, pipe the signals straight
   into ops-intake, with an `intake-signals-jsonl` source the human configured there (for
   example `growth`). Run the pipe under `set -o pipefail`, because without it a failed
   `signals` gives an empty stdin that ops-intake imports as success:
   `set -o pipefail; growth signals --source growth | intake import --format intake-signals-jsonl --source growth`,
   where `intake` is ops-intake's tool expanded as that skill says. Check the pipe's exit
   status: anything other than 0 is a failure to report. Do not read the lines first and do
   not edit them. From there ops-intake ranks the item, and the human picks it.
6. **A plan edit.** If the human changes a threshold, the plan is STALE until they register it
   again: `growth register --by "<their name>" --reason "<their words>"`. `register` also
   needs `--reason` when the registration state was deleted or emptied while the log or the
   data still remembers it. You MUST NOT edit a
   registered `growth/metrics.md` without the human, because the edit makes the plan STALE and
   moves the goalposts after the data is in.

## The plan

`growth/metrics.md` holds one Markdown table whose header names the columns. For `up`
(higher is better) `breach` is at most `warn`; for `down`, at least `warn`. `min_n` is the
smallest sample count that makes a value count; `severity` (1 to 4, default 3) is the intake
severity of a breach. Prose around the table is free: only the parsed thresholds are hashed,
so a prose edit does not make the plan STALE. The full grammar, the data format, every
`PROBLEM:` reason and the signal line are in `references/metrics-format.md`; the commands and
flags are in `references/parameters.md`.

## Statuses

`review` judges the **latest complete period** of each metric by the UTC clock: yesterday for
`day`, the last ISO week for `week`, last month for `month`. It never falls back to an older
period, so a stopped export shows NO_DATA.

| Status | When |
|---|---|
| `OK` | the value is on the good side of `warn` |
| `WARN` | at `warn`, or between `warn` and `breach` |
| `BREACH` | at `breach` or past it |
| `NO_DATA` | no value for that period, or `min_n` is set and `n` is missing or below it |
| `+stale` | a flag: the plan changed since registration, or it is missing or does not lint |

**STALE does not silence an alarm.** While the plan is STALE the review uses the thresholds
frozen at registration, marks every line `+stale`, still counts and signals each breach under
them, says `stale=yes`, and exits 3. An edit to `growth/metrics.md` therefore cannot empty the
queue.

A record for a period already imported is a no-op when `value` and `n` are the same, and a
correction when either differs: the newer record wins and the log keeps both.

## Into the queue

`signals` prints one line per metric in BREACH and nothing for WARN, NO_DATA or STALE alone,
because a metric that cannot be judged is not a problem with the product. Each line has
`source_id` = the metric name, `kind: issue`, the title `metric: <name> breached`, the plan's
severity, `first_seen` = the end of the first period in the current breach streak and
`last_seen` = the end of the latest breached period (RFC 3339 UTC), and one evidence entry for
the latest breached period only, keyed `<metric>:<period>`. It has no `url`; ops-intake
accepts that.

What ops-intake does with it (checked against ops-intake on main):
- One metric is one item, and its trust is `low`. Each new breached period adds one
  evidence key and raises the item's count by one; a second run on the same day adds nothing.
  25 breached days reviewed once a day give a count of 25.
- If the human dismisses the item, a re-run for the same period keeps it dismissed. The next
  breached period ends after the dismiss time, so the item comes back as `new +regressed`.
- A later OK or WARN period does not close the item. The human resolves or dismisses it in
  ops-intake.

## Trust and safety

- You MUST NOT send metric values, revenue or user counts to Jev or to any other model,
  because they are business data that the data guardrails cover. The tool calls no model.
- You MAY run a read-only query or export tool with the human's own login to get the data,
  because the human's permission prompts show each call. You MUST NOT change anything in an
  analytics, payment or store account, because those are the owner's live systems: no new
  event, property, dashboard, cohort, flag, price or payment.
- You MUST NOT publish, post, email, change a price or spend money, because those actions are
  `external_message` or `spend` class, which no autonomy grant covers. You MAY draft launch or
  pricing text for the human, who decides and acts.
- You SHOULD run factory-conductor's money-ledger check (`ledger.py check`) before you put any
  spend to the human, when that skill is installed, because it counts the budget's caps and
  the open reservations. Its pass is not approval: the human still decides each spend.
- You MUST NOT copy metric values into a tracked file, because revenue and user counts are
  business data. The data and the frozen plan live under the git-ignored
  `.skill-contract/growth/`, which the tool proves before its first write. The plan in
  `growth/metrics.md` is the human's to commit, and its thresholds can reveal revenue targets:
  say so before they commit it.
- You MUST NOT pass `--today` outside a test, because it moves the clock that decides which
  period is complete and which record is from the future. Outside the test suite the tool
  refuses it (`STOP:`, exit 2).
- You MUST NOT edit or delete files under `.skill-contract/growth/` by hand, because the
  registrations and the data log are the record that every review reads back. Use the
  commands; report a problem instead.
- **Names are claims.** A `--by` name is what someone typed, and an agent with a shell can type
  any name. The refusal of `agent:` names and the human's confirmation in step 2 are the
  controls.

## Unattended mode

The review itself is mechanical: with a registered plan and an export the human set up, you
can import, review and pipe the breaches into ops-intake without stopping. Registering or
changing thresholds, exporting with the human's login, and acting on a breach (picking,
dismissing, launching, pricing) wait for the human. When the user wants little supervision,
ask for those decisions upfront, then stop and report.

## Deliverable

A **growth report** for the user:
- the plan path, its `LINT:` result and the `REGISTERED:` line (who registered it, and when);
- the `IMPORT:` line and every `PROBLEM:` line, in plain words;
- each `METRIC:` line as a table (metric, status, value, period), with every `+stale` flag,
  `NOTE: stale` line and `CORRECTED:` line called out;
- the `REVIEW:` line, and for each breach the `IMPORT:` line ops-intake printed;
- what waits for the human: a plan edit to register, an item to pick or dismiss.

## Verify and repair

Before you report, run `growth review` again and check that every status you report matches a
`METRIC:` line. After a pipe into ops-intake, check that the pipe exited 0, that its `IMPORT:`
line shows `problems 0`, and that its `ok <k>` count equals both the `SIGNALS: <k>` line on
`signals`' stderr and the `breach=` count on the `REVIEW:` line. A mismatch or a problem there
is a tool bug, so report it and do not edit the lines by hand. If `lint` fails,
fix the plan with the human and lint again. If `register` refuses, read its `STOP:` and `NOTE:`
lines with the human.

## Boundaries

- **Not an analytics client.** The tool opens no connection and reads only files.
- **Not ops-intake.** It writes signal lines; ops-intake owns the queue, the ranking and the
  handoff to planning.
- **Not a launch or pricing tool.** Launch and pricing stay manual stages.
- **No statistics.** No anomaly detection, forecast or significance test; the thresholds are
  the human's judgment.
- **No envelope.** The output is ops-intake's existing `intake-signals-jsonl` format.
