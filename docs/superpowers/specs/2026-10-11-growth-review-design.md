# growth-review: pre-registered metric review into the intake queue (roadmap step 6D, part 1)

**Status:** Designed unattended on 2026-10-11, the same way as steps 6A to 6C. Jev scored each decision, and the Opus advisor reviewed the uncertain ones. The owner asked to "continue with the remaining skills / steps for the software factory". Growth and monetise is the only stage that is still missing (AC8 re-judge, 2026-10-11).

**Goal:** The human exports product metrics (signups, activation, conversion, revenue, churn and so on) to a file. A local tool checks each metric against thresholds that were registered before the data came in. It reports OK, WARN, BREACH, NO_DATA or STALE for each metric. A breach becomes a signal line that ops-intake imports through its existing `intake-signals-jsonl` format. So a falling metric reaches the same ranked queue and the same path to a plan as a failed release.

The tool runs no command, opens no connection, holds no token and calls no model. Nothing publishes, posts, emails, changes a price or spends money.

## Decisions made unattended

| # | Decision | Jev | Notes |
|---|---|---|---|
| G1 | **Structure:** a small new skill, `growth-review`, for the metric review. The project money ledger and the refuse-only pre-spend check go into factory-conductor, in a separate change (part 2). | hybrid 0.74 (conf. 0.61) | Over "only extend existing skills" (0.24) and "one big growth skill" (0.02). |
| G2 | **Order:** the metric review first. | 0.83 (conf. 0.74) | Ledger 0.04, launch 0.13. Fit for house style: metrics 1.93 (conf. 0.90), ledger 1.95 (conf. 0.93). |
| G3 | **No launch or pricing checklist in this step.** It stays a manual stage in the README. | 0.54 (uncertain); fit 1.22 (conf. 0.22); the advisor concurred | Mostly judgment. A template with a linter would check headings, not quality. Revisit when a real launch exists. |
| G4 | **Breaches go to ops-intake through `intake-signals-jsonl`** with `kind: issue`. No new intake kind. | design, not a judgment | The format is already on main. This keeps 6D independent of PR #82. |

No metric value, revenue figure or analytics record went to Jev. Only the design options above did.

## 1. The metrics plan

The plan is Markdown in the repository at `growth/metrics.md`. Each metric is one row in a table:

| Field | Meaning |
|---|---|
| `metric` | kebab-case name, at most 64 characters |
| `direction` | `up` (higher is better) or `down` (lower is better) |
| `period` | `day`, `week` (ISO week) or `month` |
| `warn` | the value that gives WARN |
| `breach` | the value that gives BREACH. For `up` it is at most `warn`; for `down`, at least `warn` |
| `min_n` | optional: the smallest sample count that makes a value count. Below it, the period is NO_DATA |
| `severity` | 1 to 4, the intake severity of a breach. Default 3 |

`growth lint` checks the table. `growth register` stores a frozen copy of the thresholds with a hash and a date. A threshold edit after register makes the plan STALE until the human runs `register` again with `--reason`. This is the same pre-registration idea as idea-validation: you cannot move the goalposts after you see the number without a visible record.

**STALE never silences an alarm.** While the plan is STALE, the review uses the frozen thresholds, not the edited ones, and marks every line `+stale`. A breach under the frozen thresholds is still a breach, and it still becomes a signal. Otherwise an edit to `growth/metrics.md`, by the agent or anyone else, would empty the queue, and the factory acts only on the queue.

## 2. The data file

The human exports the data. The agent may transform an export into this format. JSON lines, one value for each metric and period:

```
{"metric": "<name>", "period": "2026-10" | "2026-W41" | "2026-10-11", "value": <number>,
 "n": <optional sample count>, "source": "<where it came from>"}
```

- A record for an unknown metric, a period that does not fit the metric's `period`, a value that is not a finite number, or a period later than today is a `PROBLEM:` line. The other records still import.
- The same (metric, period) seen again with the same value is a no-op. With a different value, it is a correction: the newer value wins, and the log keeps both. When a correction moves a period out of BREACH, the review prints a `CORRECTED:` line for it, so a breach cannot vanish without a record.
- Periods end in UTC, and "today" is UTC. A period that ends more than 1 day after today is a problem record. The 1-day grace matches ops-intake's future-date rule and lets an export made east of UTC through.
- The data goes into the git-ignored `.skill-contract/growth/`, because revenue and user counts are business data.

## 3. The review

`growth review` evaluates the latest complete period of each metric:

- **OK:** the value is on the good side of `warn`.
- **WARN:** between `warn` and `breach`.
- **BREACH:** on or past `breach`.
- **NO_DATA:** no value for the latest complete period, or `n` below `min_n`.
- **+stale:** a flag, not a status. The thresholds were edited since register, so the review used the frozen copy.

It prints one `METRIC: <name> <status>[ +stale] value=<v> period=<p>` line for each metric and a `REVIEW:` summary line that counts each status and says `stale=yes|no`. The exit code is 0 when nothing is in BREACH and the plan is not STALE. It is 3 when something is in BREACH or the plan is STALE (fail-closed, like factory-conductor's `budget_unmetered`). Exit 2 is for bad input.

A period is "complete" when it has ended, by the clock (or `--today` in tests only, with the same test-only guard as idea-validation 0.1.1).

## 4. Into the queue

`growth signals --source <name>` prints one `intake-signals-jsonl` line for each metric in BREACH:

- `source_id` is the metric name, and `kind` is `issue`;
- `title` is `metric: <name> breached`;
- `severity` comes from the plan;
- `first_seen` is the end of the first period in the current breach streak, and `last_seen` is the end of the latest breached period. Both are RFC 3339 timestamps in UTC with `Z`;
- the line carries evidence for **the latest breached period only**. Its key is `<metric>:<period>`, and its text is `value <v> vs breach <b> (<direction>)`.

One evidence entry per run keeps the evidence small and gives one key per breached period. Dedupe on main uses ops-intake's uncapped, source-namespaced `seen_keys`, so a key it already holds adds nothing. A metric that breaches every day for 25 days and is reviewed every day adds one key a day, never a repeat. (Corrected in the review round: see erratum 15.)

The agent pipes it into `intake import --format intake-signals-jsonl --source <name>` without reading it first. Whether a later breach regresses a dismissed item follows ops-intake's own recurrence rule for `intake-signals-jsonl`. The build checks this against main and states the result in the SKILL.

WARN never becomes a signal. STALE and NO_DATA never do either. A metric that cannot be judged is not a problem with the product.

## 5. Trust and safety

- **No metric data to any model or service.** You MUST NOT send metric values, revenue or user counts to Jev or to any other model, because they are business data that the data guardrails cover. The design questions in this document carried no figures.
- **Read-only exports.** The agent MAY run a read-only query or export tool with the human's own login to get the data. It MUST NOT change anything in an analytics, payment or store account, because those are the owner's live systems.
- **Nothing outward.** The skill never publishes, posts, emails, changes a price or spends money. It MAY draft launch or pricing text for the human.
- **Thresholds are the human's.** The agent MAY propose thresholds, and only the human registers them, because a goalpost the agent sets for its own work proves nothing. `register` takes `--by NAME` and refuses `agent:` names, as idea-validation does. The agent MUST NOT edit a registered plan without the human, because the edit makes the plan STALE and moves goalposts after the data is in.

**Known limits:**
- Read-only use of analytics MCP tools is not enforced. The human's permission prompts are the control, as in ops-intake.
- `--by` names are self-declared, not signed.
- The committed plan can reveal revenue targets, even though the data is git-ignored.

## 6. Handoff

No new envelope kind. The output is the existing `intake-signals-jsonl`, and ops-intake carries it into `intake-item/v1` from there. A later version can add a `growth-review/v1` envelope if a consumer needs one.

## 7. Tests, eval, gates

- Plan lint: a good table; a bad direction; `breach` on the wrong side of `warn`; a duplicate metric; a bad name.
- Register and stale: an edit after register makes the review `+stale` with exit 3; a breach under the frozen thresholds still gives a signal even when the edit would hide it; register again with `--reason` clears it.
- Corrections: a correction that moves a period out of BREACH prints `CORRECTED:`.
- Count: 25 daily breaches reviewed once a day, plus a second run on the same day, give an ops-intake count of 25 with main's adapter.
- Data: unknown metric, wrong period shape, NaN, future period, correction (newer value wins, log keeps both), and a re-import that changes nothing.
- Review: one fixture each for OK, WARN, BREACH, NO_DATA (missing, and `n` below `min_n`), up and down directions, and the boundary values exactly at `warn` and at `breach`.
- Signals: a breach gives one line that ops-intake's `intake-signals-jsonl` adapter accepts. The test imports ops-intake's adapter from the sibling directory. A WARN gives no line.
- `--today` outside the test switch is refused.
- No-I/O test: the tool opens no socket and runs no subprocess.
- Eval: a positive flow (plan, register, data, review, signals that ops-intake parses) and negatives (a moved goalpost, an agent-registered plan, a future period, a WARN that leaks as a signal).
- A/B rows under `SINCE_GROWTH_REVIEW`. README: the Growth / monetise row moves from missing to partly, and the new skill goes into the catalog.

## 8. Out of scope

- the project money ledger and the refuse-only pre-spend check (part 2, in factory-conductor);
- a launch or pricing checklist (G3);
- vendor adapters (Mixpanel, Stripe, App Store, PayPal), which wait for a real sample and a tool that owns auth;
- anomaly detection, forecasting and statistical tests (the thresholds are the human's judgment);
- making `spend` grantable (S7, the owner's decision).

## Errata (build, 2026-10-12)

The build follows this design. These points changed or were made exact while building it.

1. **The 1-day grace is not ops-intake's rule on main.** (Replaced in the review round:
   see erratum 21. There is no grace now.) Section 2 says the grace "matches
   ops-intake's future-date rule". `ops-intake/assets/intake.py` on main has no future-date
   rule. The grace stays as designed: a period whose last day is more than 1 day after today
   (UTC) is a problem record. A side effect: a month or ISO week that is still running is
   refused until its last day is at most 1 day away. The format reference says so, so the
   agent exports finished periods only.
2. **"The end of a period" is the start of the next day.** Day `2026-10-11` ends at
   `2026-10-12T00:00:00Z`; week `2026-W41` at `2026-10-12T00:00:00Z`; month `2026-09` at
   `2026-10-01T00:00:00Z`. `first_seen` and `last_seen` use this.
3. **The latest complete period comes from the clock.** Yesterday, the last ISO week or last
   month. With no record for it, the status is NO_DATA; the review never falls back to an
   older period. A breach streak runs back from that period and stops at a gap.
4. **No `url` in the signal line.** Main's adapter reads `url` as optional and stores `""`.
   There is no natural URL for a metric. A test pins that main accepts the line.
5. **`signals` keeps stdout pure.** Only JSON lines go to stdout; `NOTE:` and `STOP:` go to
   stderr. It exits 0 when it printed its lines, breaches included, so a `pipefail` pipe does
   not break. `review` is the alarm (exit 3).
6. **`import` exits 3 on problem records**, as ops-intake's import does. The other records
   still import. Exit 2 stays for refused input (no registration, an unreadable file).
7. **Import checks records against the frozen copy.** A record for a metric that was added
   to `growth/metrics.md` but not registered is a problem record until the human registers.
8. **`min_n` with no `n`.** A record without `n` for a metric with `min_n` imports, and the
   review counts it as NO_DATA. This keeps import independent of a later `min_n` change.
   (Changed in the review round: when that record is for the judged period, the review
   prints a `NOTE:` and exits 3. See erratum 17.)
9. **A correction is a different `value` or `n`.** A different `source` alone is a no-op.
10. **STALE covers a broken plan.** A missing `growth/metrics.md`, or one that does not lint,
    is STALE too, and the frozen thresholds stay in force. A metric removed from the file is
    still reviewed; a metric added to it is not, until it is registered. The hash covers only
    the parsed thresholds, so a prose edit is not STALE.
11. **`CORRECTED:` covers every stored period**, not only the latest one, so an older breach
    cannot vanish without a record either.
12. **Recurrence on main, as built and tested.** A dismissed item becomes `new +regressed`
    when a signal's `last_seen` is later than the dismiss time. A re-run for the same period
    has an earlier `last_seen`, so the item stays dismissed; the next breached period brings
    it back. A later OK or WARN period does not close the item. The SKILL states this, and
    `assets/test_growth_signals.py` pins it.
13. **No skill-contract opt-in.** The skill emits no envelope (section 6), so it carries no
    `skill-contract` metadata and no `## Contract` block.
14. **The `--today` guard's STOP line does not name the switch.** idea-validation 0.1.1 names
    its variable in the STOP line; growth-review prints only `STOP: --today is for tests
    only`. The README names the variable for humans.

## Errata (review round, 2026-10-12)

An independent review of the build found the points below. Each one changes behaviour or
corrects this document. `assets/test_growth_hardening.py` pins them.

15. **The reason for one evidence entry (section 4).** Section 4 said dedupe on main looks
    only at the capped evidence list, and that PR #82 fixes the cap. That is out of date.
    Main now has ops-intake's uncapped, source-namespaced `seen_keys`, and dedupe uses them.
    The reason for one evidence entry per run is that it keeps the evidence small and gives
    one key per period. Section 4 is corrected.
16. **A reset by hand needs a reason (M1).** If `plan.json` is deleted, or its
    `registrations` list is empty, `register` with no `--reason` is refused (`STOP:`, exit
    2) when `log.jsonl` records an earlier registration or `data.jsonl` exists. Before, it
    reset the thresholds without a trace in the state. `review` cross-checks `plan.json`
    against `log.jsonl`. When the latest registration differs, or one file records a
    registration that the other does not, it prints a `NOTE:` and exits 3. With no
    registration in `plan.json` but one in the log, `review` and `signals` exit 3 (before:
    2).
17. **Corrections and `n` fail closed (M2).** `CORRECTED:` adds ` n was=<a> now=<b>` when
    `n` changed, so an n-only correction is visible. `review` exits 3 when a `CORRECTED:`
    line concerns a metric's judged (latest complete) period. It also exits 3, with a
    `NOTE:`, when the judged period has a record but `min_n` is set and `n` is missing or
    below it. Before, both turned a BREACH into NO_DATA with exit 0. A correction of an
    older period still prints `CORRECTED:` and does not change the exit code.
18. **ASCII digits only (M3).** The number, whole-number and period patterns use `[0-9]`.
    `\d` matched fullwidth and other Unicode digits, and `int()` and `float()` accept them,
    so a fullwidth period imported and was never judged. Such a period is now a problem
    record, and such a threshold or `min_n` fails lint.
19. **Huge numbers (M4).** An integer `value` too large for a float raised `OverflowError`
    and stopped the whole import. It is now `PROBLEM: line N: value must be a finite
    number`, and the other records import. `n` and `min_n` are capped at 18 digits. A
    threshold that overflows a float was already refused.
20. **`signals` is countable (M5).** `signals` prints a last stderr line `SIGNALS: <k>`.
    The SKILL runs the pipe under `set -o pipefail`, checks its exit status, and checks
    that ops-intake's `ok <k>` equals `SIGNALS: <k>` and the `REVIEW:` breach count. A
    failed `signals` prints nothing on stdout and no `SIGNALS:` line.
21. **No future periods at all (M6, L7).** A period whose last day is after today (UTC) is
    refused: `period has not ended: its last day is after today (UTC); export finished
    periods only`. The 1-day grace of erratum 1 is gone. A day label for today itself still
    imports, for an export made east of UTC. Tomorrow's label is refused, because it hid a
    breach once the clock made it the judged day. Weeks and months are tighter than before
    too: they import from their last day on, not one day before it. The old text ("ends more
    than 1 day after today") did not match `period_end`, which is why it changed.
22. **Corrupt registrations (L1).** Each registration entry is checked on load: `sha256`,
    `by`, `at` and `metrics` present, each metric well formed, and the thresholds match their
    sha256. A bad entry is `STOP:` and exit 2 in every command. Before, `{}` gave a
    `KeyError` traceback (exit 1). Stored data records are checked the same way. An integer
    too large for a float in `plan.json` or `data.jsonl` is a bad entry too (`STOP:`, exit
    2), not an `OverflowError` traceback.
23. **Lookalike agent names (L2).** The `agent:` check folds the name with NFKC, a small
    map of Cyrillic and Greek lookalike letters and `casefold()`, then refuses
    `^\s*agent\s*[:/\-_]`. So `agent：claude`, `ａｇｅｎｔ:claude`, `аgent:claude`
    (Cyrillic `а`), `agent :claude`, `agent-claude` and `agent/claude` are refused;
    `Agent Smith` passes. Follow-up: idea-validation's `_is_agent()` has the same gap and is
    not changed here.
24. **Who registered (L3).** `REGISTERED:` includes the time. `review` prints
    `NOTE: registered <sha12> by <name> at <time>[ reason <reason>]`.
25. **Symlinked state files (L4).** `plan.json`, `data.jsonl`, `log.jsonl` and `.lock` are
    refused when they are symlinks (`STOP:`, exit 2). `register` and `import` check before
    the first write, so a refusal never leaves `plan.json` and the log out of step. Files are
    opened with `O_NOFOLLOW` where the platform has it, after an `lstat` check.
26. **Not done: a note for data imported before its period ended.** The review suggested an
    optional `NOTE:` when the judged record was imported before its period ended. It is not
    built: `imported_at` comes from the real clock, not the test clock, so the check cannot be
    tested without mocking time, and erratum 21 already limits such records to today's own
    day label.
