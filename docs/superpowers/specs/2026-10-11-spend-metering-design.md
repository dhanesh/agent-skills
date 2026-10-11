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
