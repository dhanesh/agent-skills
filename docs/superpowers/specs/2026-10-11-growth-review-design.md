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

One evidence entry per run keeps ops-intake's count right on main, where dedupe looks only at the capped evidence list (PR #82 fixes the cap for every source). A metric that breaches every day for 25 days and is reviewed every day adds one key a day, never a repeat.

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
