# Spend metering: factory-conductor enforces token and dollar caps (roadmap step 6B)

**Status:** Designed unattended on 2026-10-11, the same way as step 6A (#80): Jev scored each decision and the Opus advisor reviewed it. This change is stacked on #80. The owner reviews it in the PR.

**Context:** An autonomy grant carries `budget.max_tokens` and `budget.max_usd`. Today factory-conductor records them and reports them, but does not enforce them. Its SKILL.md says: "Not a spend governor. Token and dollar caps are recorded, not enforced." The reason is that the stdlib tool cannot see usage. The harness can, though: Claude Code reports each subagent's token count when the subagent finishes. The README's Governance row says "No spend governor".

**Goal:** When a grant asks for it, an unattended run stops at its token or dollar cap, and it stops when nobody reports usage. Every change only tightens. `spend` stays an ask-class action that no grant covers.

## Decisions made unattended

| # | Decision | Jev | Notes |
|---|---|---|---|
| S1 | **Enforcement stops the run.** `next`, `resume` and `start` stop with `STOP: budget_tokens` or `STOP: budget_usd` (exit 3) when the run's metered total reaches the cap. | 0.96 | warn only: 0.04 |
| S2 | **Fail closed when unmetered.** When enforcement is on and a finished dispatch has no usage entry, the conductor refuses the next dispatch with `STOP: budget_unmetered`. | 0.99 | The advisor asked for a per-dispatch rule, not a tunable N. |
| S3 | **Run caps live in factory-conductor**, not in a new skill. | Jev 0.57, uncertain (conf. 0.14); decided by the advisor's reasoning pass | The STOP must fire inside the conductor, which already owns the run state, the budget keys and the dispatch count. |
| S4 | **Metering source:** the orchestrating agent reports each dispatch's usage from the harness (`usage` command, trust `claimed`). Optionally, a JSON-lines file from the user's own transform adds entries (trust `low`). There is no price table and there are no provider billing adapters. | Jev 0.64 for "both", uncertain (conf. 0.46); the advisor narrowed it | Prices drift and cannot be checked unattended. Billing adapters follow ops-intake's no-guessed-shapes rule and wait for real samples. |
| S5 | **Opt-in per grant.** A new budget key, `enforce: ["tokens"]`, `["usd"]` or `["tokens", "usd"]`, switches it on. Without the key, the behaviour is the same as today. | advisor | Existing grants have caps that were recorded only. Fail-closed would stop every one of those runs. |
| S6 | **Parked:** a project-level money ledger and a refuse-only pre-spend check. | precheck P = 0.67 (uncertain) | No built skill spends money yet. These land with the growth step, which is their first real consumer. |
| S7 | **Not decided, owner question:** should `spend` ever become grantable (amending A8)? This change does not touch it. | — | The owner's decision. |

## 1. Behaviour

### 1.1 Budget key

`budget.enforce` is a list with one or both of `"tokens"` and `"usd"`, and no repeats.
- `init` refuses an `enforce` that names a cap the budget does not set. For example, `enforce: ["usd"]` without `max_usd` is refused with exit 2.
- With no `enforce` key, nothing changes. The `BUDGET_NOTE` line, the tests that pin it, and the A/B rows all stay as they are.
- With `enforce`, the status note says which caps are enforced and which are only recorded.

`write_grant.py` already passes `budget` through. spec-first-planning's unattended round gains one question: "Enforce the token cap? (Your agent must report each dispatch's usage. Claude Code shows it when a subagent finishes.)"

### 1.2 Recording usage

`conductor.py usage <task> --tokens N [--usd X] [--source TEXT]` records one entry for the latest finished dispatch of that task that has no entry yet.
- An entry is `{task, dispatch, tokens, usd, source, trust: "claimed", at}`.
- The command refuses (exit 2) when that task has no finished dispatch without usage, so one dispatch cannot be metered twice. It also refuses a negative or non-finite number.
- `usage --file F` reads JSON-lines records `{task, tokens, usd, source}`. Each record is applied the same way, with `trust: "low"`. A bad record is reported and skipped. The other records still apply.

Entries go to the run state (atomic replace) and to the append-only run log.

### 1.3 Dispatches and "finished"

A dispatch is the executor, repair, reviewer or verifier work that `resume` or `start` hands out, and that `max_dispatches` already counts.
- A dispatch is **in flight** from the moment it is handed out until the conductor records its outcome: `verify`, `review` or `evidence` for that task, or `park`.
- Once the outcome is recorded, the dispatch is **finished**.

The implementer maps this onto the existing dispatch bookkeeping. If the mapping needs a different rule, the errata records it.

### 1.4 Enforcement

When `enforce` is set, every command that can hand out a dispatch (`next`, `resume`, `start`) checks the following, before its existing checks and in this order:
1. `budget_unmetered`: some finished dispatch has no usage entry, or has an entry with no figure for an enforced metric (for example, `usd` is enforced and the entry has no `usd`).
2. `budget_tokens`: the sum of `tokens` is at or above `max_tokens`, when tokens are enforced.
3. `budget_usd`: the sum of `usd` is at or above `max_usd`, when usd is enforced.

The stop is the same kind as `budget_wall_clock`. Work in flight continues to its outcome, and nothing new is handed out. `status` prints the totals, the caps and how many dispatches are unmetered. The `run-result/v1` payload gains `usage: {tokens, usd, entries, unmetered, trust}` and keeps its `budget_note`. The PR body shows the totals.

Usage totals are claimed numbers, so the cap is only as good as the reports. The SKILL says this. The fail-closed rule makes sure an agent that stops reporting cannot run on without limit.

### 1.5 Scheduled re-entry

`watch` and `resume` follow the same rules. A resumed session first records the usage it can see, then resumes. If it cannot see the usage, the run stays stopped at `budget_unmetered`, and the human can lift the stop. `--budget` cannot loosen `enforce` after `init`, the same as the other budget keys.

## 2. Changes

- `factory-conductor/assets/conductor.py`: the `enforce` key, the `usage` command, the three stop reasons, the status lines, and the `run-result/v1` usage field. The version goes up one minor version.
- `factory-conductor/SKILL.md`: when the grant enforces spend, after each finished dispatch, the agent MUST record the usage that the harness reports before it asks for the next action, because the run otherwise stops at `budget_unmetered`. The agent MUST NOT invent or estimate a usage figure, because the cap would then be fiction. The "Not a spend governor" boundary is updated. BCP 14 register rows are added.
- spec-first-planning: the unattended round's question list (SKILL.md and its reference), and the grant answers example. There is no code change unless write_grant validates budget keys (it does not today).
- `docs/skill-contract/SPEC.md`: the optional `usage` field in `run-result/v1`, and `budget.enforce` in `autonomy-grant/v1`.
- README: the Governance row and the factory-conductor limits.
- Tests (offline): each stop reason; opt-in off gives byte-identical behaviour; a refused double meter; a bad `enforce`; a JSONL import with one bad line; the resume path; the run-result usage field.
- Eval: a positive case (an enforced run stops at the token cap) and negatives (an unmetered dispatch stops the run; no `enforce` key means no change).
- A/B rows under `SINCE_SPEND_METERING`.

## 3. Acceptance criteria

| AC | Done when |
|---|---|
| AC1 | A grant with `enforce: ["tokens"]` stops at `max_tokens` with `STOP: budget_tokens`. |
| AC2 | A finished dispatch without usage stops an enforced run with `STOP: budget_unmetered`. |
| AC3 | A grant without `enforce` behaves exactly as before (the existing tests and A/B rows are unchanged). |
| AC4 | The run-result envelope and the PR body report the usage totals and their trust. |
| AC5 | `make gate` (CI) and `make ab-validate` are green. |

## 4. Out of scope

- making `spend` grantable (S7, the owner's decision);
- the project money ledger and the pre-spend check (S6, with growth);
- converting tokens to dollars, and provider billing adapters.

## Errata (build)

The build (2026-10-11) kept the design. These points make it exact where the code needed a rule. Jev judged E1 and E3 in one call: E1 chose the `budget_dispatches` pattern (P = 0.86, confidence 0.72), and E3 kept fail-closed (P = 0.73).

- **E1. In-flight work (§1.4), rewritten by the spend review (M1).** The spec says the stop is "the same kind as `budget_wall_clock`" and also that "work in flight continues to its outcome". In the code, a recorded stop makes `verify`, `review` and `merge` refuse, so the wall-clock pattern strands in-flight work. The build follows `budget_dispatches` instead. A spend stop is recorded only when a task is ready and nothing is in flight. While work is in flight, `next` prints no `READY:` and `start` refuses (exit 2, logged as `dispatch_refused`). The first build also still handed out repairs and verifier redos to in-flight tasks. The review measured 42 unmetered dispatches in one run with `max_repairs_per_task` 20, so that was not a bound. Now, while `spend_stop` fires, no repair goes out. A failing `verify`, `review` or `evidence` check parks its task with the stop's reason, the same way that `_dispatch` parks with `budget_dispatches`. A verifier redo parks too, and so does a `dispatch-executor` or `dispatch-repair` line that `resume` would print. Only the reviewer and the verifier of each in-flight task's current attempt still go out. So the run overshoots a stop by at most two dispatches per in-flight task. A crash re-dispatch of a reviewer or verifier by `resume` can add one more each time; `max_dispatches` still bounds those. A task parked this way stays parked. When nothing is ready, the run ends with `no_ready_tasks` as before.
  - **The grace rule.** The outcome that sends a task back (`verify`, `review` or `evidence`) has just finished a dispatch, and the agent can record its usage only after that outcome. So the check at send-back time leaves out the dispatches of that task that the newest log event finished. Without the rule, every failing verify or review in a fully reported run would park. An older unmetered dispatch, of any task, still parks it. `resume` applies no grace, because no outcome was just recorded. Jev judged the rule sound (P = 0.70), chose to park both the executor and the repair lines in `resume` (0.73, confidence 0.59, below 0.6, so the advisor's reasoning decided it: the bound needs both), and chose the reporting below (0.98).
- **E2. Who hands out a dispatch (§1.3).** `verify`, `review` and `evidence` also hand out dispatches (a reviewer, a repair, a verifier), not only `resume` and `start`. All of them count. Only `next`, `start` and `resume` record a spend stop. Since the spend review (E1), `verify`, `review` and `evidence` also check the spend stops before they send out a repair or a verifier redo, and park the task when one fires; their reviewer and verifier for the current attempt still go out. A dispatch is one `dispatch` event in the run log, and its id is the `dispatches` ordinal that the event carries. It is finished once a later `verify`, `review`, `evidence` or `park` event names its task. The dispatch list is read from the log, so `state.json` gains only `usage`, and only once there is an entry.
- **E3. A crash re-dispatch.** After a crash, `resume` hands an in-flight executor out again, and one `verify` then finishes both dispatches. The new session can report only its own subagent, so the older dispatch stays unmetered and the run stops at `budget_unmetered` until a human records it. This is deliberate (fail closed). `usage` meters the latest unmetered finished dispatch first.
- **E4. Lifting (§1.5).** `resume` lifts a `budget_unmetered` stop once every finished dispatch is metered, the same way that it lifts `grant_ask`. While a dispatch is still unmetered, it prints `NEXT: run ask`, not `run finish`. `watch` treats the stop as waiting on the human (`REENTRY: waiting-human`), because a started agent cannot see the old usage and would end the run. `budget_tokens` and `budget_usd` are not lifted.
- **E5. Records and exit codes (§1.2).** `tokens` is required in every entry, from the CLI and from the file, except a human's `unknown` entry (E5c). `usd` is required when `usd` is enforced, because an entry without it would leave the dispatch unmetered for good. NaN, Infinity, booleans and negative figures are refused. `usage --file` prints `PROBLEM: line <n>: <why>` per bad record and exits 3 when there was one (ops-intake's convention); it exits 2 when the file cannot be read. `usage` refuses a finished run, and works on a stopped one.
- **E5a. Dispatch-bound records (spend review M2).** A file record must name its `dispatch`, the id that `status` prints as `STATUS: unmetered <task> dispatch=<n>`. The record meters exactly that dispatch. It must be the task's, be finished, and have no entry yet; otherwise the record is a `PROBLEM:` and is skipped. Before this, a record meant "the task's latest finished unmetered dispatch", so a transform's file that was appended to and applied again metered the reviewer with the executor's figure. Now each old line is a `PROBLEM:`. The CLI takes an optional `--dispatch N`; the default stays the latest finished unmetered dispatch.
- **E5b. Finite totals (spend review L1).** One figure, and the run's total, is at most 10^12 tokens and 10^9 USD. `usage` refuses an entry that passes either, from the CLI and from a file. Two 1.7e308 entries made the usd total Infinity before, and the envelope held it.
- **E5c. A human's unknown entry (spend review L2).** `usage <task> --unknown [--dispatch N] --by NAME` records trust `unknown` with no figures and the human's name. It clears that dispatch from `budget_unmetered`, also when `usd` is enforced. The totals leave it out. `entries` and `trust` cover the entries with figures, and a new `usage.unknown` counts the attested ones in the run result; `status` adds `unknown=<n>` and the PR body adds a line when there is one. The SKILL says that only a human runs it (MUST NOT for the agent, because it removes the meter). On a run stopped for good (any stop but `grant_ask` or `budget_unmetered`), `usage` also meters a dispatch that is still in flight, finished ones first, so the totals do not leave out the work that the stop cut short. A `grant_ask` stop is excluded, because the in-flight subagent goes on once the grant is renewed and its own report must still be recordable.
- **E5d. Where the figures come from (spend review L3).** Claude Code shows a token count per subagent, not dollars. An enforced `usd` cap needs a user's transform (`usage --file`) or a harness that reports cost. spec-first-planning's unattended round now says so.
- **E6. Reporting.** The run result's `usage` field, the `STATUS: usage` line and the PR body's `Usage:` line appear when the budget sets `enforce` or an entry exists. Without them, the output is unchanged. `usage.usd` is null when no entry carries a `usd` figure, and `trust` is the weakest entry's (`low` before `claimed`). Since E5c, `entries` and `trust` cover only the entries with figures.
