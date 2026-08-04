# Driving this skill from a self-prompting loop

A loop that infers its own position from prose drifts. This skill exposes its position as
**data**, so a loop decides what to do next by reading a field rather than by re-reading
its own output.

```bash
python3 assets/review.py state --into .review --json
```

```json
{
  "phase": "condense",
  "done": false,
  "next_action": "resolve the plan rejections, then run `draft` again",
  "blockers": ["collapse 21-23: buries the definition of `rows` ..."],
  "gates": {"plan": "rejected", "review": "absent"},
  "density_pct": 91.3,
  "unresolved_high": []
}
```

## The contract

| Field | Type | Meaning |
|---|---|---|
| `phase` | one of `unopened`, `condense`, `interrogate`, `write`, `repair`, `done` | where the run is |
| `done` | bool | **the termination condition.** Nothing else decides it. |
| `next_action` | string | the single next step, in imperative form |
| `blockers` | string[] | what stands in the way right now; empty when nothing does |
| `gates.plan` | `absent`/`rejected`/`accepted` | does the condensation plan compile |
| `gates.review` | `absent`/`fail`/`pass` | does the review pass its completeness check |
| `density_pct` | number or null | share of changed rows surviving into the condensed diff |
| `unresolved_high` | string[] | high-severity signals the review has not yet resolved |

Every field is derived from files on disk. Nothing is held in memory between invocations,
so an interrupted loop resumes at the right step instead of starting over — and two
invocations against the same directory always agree.

## The loop

```
until state.done:
    s = state --json
    perform s.next_action
```

That is the whole thing. Concretely:

| `phase` | What the agent does |
|---|---|
| `unopened` | `open` the change into a work directory |
| `condense` | read `indexed.diff`, write or fix `plan.json`, `draft`, then `commit` |
| `interrogate` | read `condensed.diff` and the signals, `template`, answer every section |
| `repair` | close the gaps in `blockers`, then `grade` again |
| `done` | stop, and hand over `REVIEW.md` |

## Why it terminates

Each phase has a gate that only closes when a real artifact exists and passes a
deterministic check: the plan compiles, the condensed diff is written, every section is
answered, every high-severity signal is resolved. The loop cannot declare victory by
asserting it — `done` is computed from those gates, never from the agent's own account of
its progress.

Two guards are worth knowing about:

- **An empty plan is called out rather than accepted silently.** A plan with no edits
  compiles, which would otherwise let a loop skip condensing entirely by doing nothing.
  `next_action` says so explicitly, and a change with genuinely no noise is a legitimate
  answer — it just has to be a decision rather than an omission.
- **Rejections become blockers, not exceptions.** A malformed plan yields
  `gates.plan: "rejected"` with the compiler's messages in `blockers`, and `state` still
  exits `0`. There is no failure mode where the loop cannot find out what to do next.

## Budgeting the loop

Condensing is the phase that can spin: a plan can be redrafted indefinitely without ever
being wrong enough to reject or good enough to satisfy. Two bounds worth setting from the
outside:

- cap redraft attempts (three is usually plenty) and `commit` the best plan you have,
  noting the density you settled at;
- treat `density_pct` as a diagnostic rather than an objective. Chasing a number produces
  a diff that hides decisions, which is the failure this skill exists to prevent.

## Composing with other loops

`state --json` is designed to be read by a supervising loop, so this skill slots in as one
phase of a larger workflow — for example: land a change, review it here, then act on the
verdict. Two useful compositions:

- **Review-the-reviewer.** When another agent has already reviewed the change, run `audit`
  first and let its blind spots seed this run's `interrogate` phase
  ([references/second-opinion.md](second-opinion.md)).
- **Gate on the verdict.** `REVIEW.md` ends with exactly one of `ship`,
  `ship-with-followups`, `needs-changes`, `needs-discussion` — a small enough vocabulary for
  a supervising loop to branch on without parsing prose.

The house conventions for building the surrounding loop — typed state, a real termination
condition, and a boundary that does not rely on the model's self-report — are in the
sibling `crafting-self-prompting-loops` skill.
