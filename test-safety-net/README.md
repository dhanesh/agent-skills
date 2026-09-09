# test-safety-net

Writes real, proven-failing-before-passing tests into a repo that has none, so an agent (or a
person) can change it without flying blind. Not a correctness audit — it pins current behaviour
even where it looks wrong, and reports the suspicion rather than silently blessing it — and never
a coverage-percentage chaser.

## The gap it fills

Three sibling skills already surround this problem and none of them solves it:

| Skill | Does |
|---|---|
| [`agent-ready-rails`](../agent-ready-rails/) | *Audits* — rail R1 "runnable verifiers" scores low when there's no real test suite |
| [`verifier-installer`](../verifier-installer/) | *Installs* the loop: format/build/test commands + CI, plus one placeholder smoke test to keep it runnable |
| **`test-safety-net`** ← this | *Fills* that loop with real tests |
| [`clean-code`](../clean-code/) | *Judges* the tests you now have (`clean-code/references/testing.md`) |

`verifier-installer` deliberately stops at one placeholder test to make the loop runnable — that
placeholder is the seam this skill occupies.

## The two disciplines

1. **Every test declares its kind.** `characterization` pins what the code does now;
   `specification` asserts what it *should* do, derived from a real source (docs, types, an
   issue). Where the two disagree, the test still pins current behaviour, and the disagreement is
   reported as a suspected bug — never silently resolved either way.
2. **Every test is proved able to fail before it counts.** Write the assertion wrong, require RED,
   correct it, require GREEN. This proves the assertion binds to real output; it does not prove
   the test catches every behavioural change — that's what true mutation testing would give.

## The four tiers

Code with no tests usually has none *because* it isn't directly testable, so this skill triages
every unit rather than assuming it's callable:

| Tier | Situation | What happens |
|---|---|---|
| 1 — direct | deps passable, output returnable, no I/O reachable | a real unit test |
| 2 — wider boundary | I/O at a boundary that can be controlled (temp dir, frozen clock, seeded randomness) | pinned at that boundary, named in the report |
| 3 — needs a seam | no honest boundary without adding code | reported to `clean-code`, never acted on here |
| 4 — not reachable | global state, import-time work, deep branch soup | a prioritized refactor reason |

Only filesystem, clock, randomness, and environment variables are controlled automatically —
database and HTTP are declined to Tier 3 by default and are only ever pinned at Tier 2 with a
recorded justification (an in-memory database, or `mockstar-mock` for HTTP), never silently.

Static triage is a **filter**, not the enforcement — Python's dynamic dispatch means source
analysis alone can't decide what a unit really touches. The invariant "never write a test that
performs real I/O" is enforced at runtime instead, by a tier-aware guard that ships with the skill
([`assets/io_guard.py`](assets/io_guard.py)) and is loaded as a pytest plugin ahead of collection
rather than written into the repo. It patches the lowest layer Python exposes — `builtins.open`,
`io.open`, the `os` primitives including the directory-and-metadata family no fd-level patch can
see, `socket.socket`, `subprocess.Popen` and the DB entry points already imported — and raises its
own exception type, so a guard trip (the classification is wrong) is never mistaken for an
assertion failure (the captured value is wrong). Full mechanism, coverage table and residuals in
[`references/triage.md`](references/triage.md).

Tiers 3 and 4 are output, not failure — a ranked "here's what blocks testing and the smallest fix"
list is the handoff to `clean-code`, and is often worth more to a human than the tests themselves.

## Install

```bash
npx skills add dhanesh/agent-skills --skill test-safety-net
```

No further setup: the bundled ranker is offline, stdlib-only python3 (git CLI needed only for the
churn signal). Python repositories only in this version — node/go/rust are on the follow-up plan
(see [`references/stacks.md`](references/stacks.md)).

## Usage

Ask the agent to "add a safety net before we touch this," or run the ranker directly to see what
it would rank:

```bash
python3 assets/rank_risk.py /path/to/repo --top-n 10 --since "6 months ago"
```

It emits deterministic JSON: `ranked` (top N by churn × blast-radius, testability-filtered),
`remainder` (everything past the cutoff — where the next run resumes), `not_netted` (Tier 3/4
seam/refactor candidates for `clean-code`), and `covered` (units some existing test already
exercises). Full argument and output-key reference in
[`references/parameters.md`](references/parameters.md).

The agent then confirms the ranked top N with you — a hard gate before anything is written — then
writes and proves each test one unit at a time, and hands back a report naming every added test,
every suspected bug (pinned, not blessed), everything it couldn't prove, and the ranked remainder.

## Layout

- `SKILL.md` — the agent-facing workflow: detect → rank → confirm → write-and-prove → report.
- `references/triage.md` — the four-tier triage, boundary controls, and the runtime guard.
- `references/stacks.md` — per-stack facts (find units / where tests go / run one test).
- `references/parameters.md` — `rank_risk.py`'s CLI flags and JSON output shape.
- `assets/rank_risk.py` — the stack-agnostic ranker: churn, approximate blast radius,
  scoring, the CLI and the JSON shape, plus the stack registry everything else hangs off.
- `assets/stack_python.py` — the Python stack: unit discovery and testability triage.
- `assets/stack_node.py` — the node/TypeScript stack: heuristic discovery of exported
  units, the naming, lexing and import-grammar answers JS gives, and node's own I/O
  marker tables and triage. Registered, so a node repo is detected and ranked — but not
  yet *written*: the runtime guard for node is still to come (`references/stacks.md`).
- `assets/stack_common.py` — the file helpers and the manifest-evidence rule the
  ranker and every stack share.
- `assets/io_guard.py` — the tier-aware runtime I/O guard, loaded as a pytest plugin via `-p`.
- `assets/test_rank_risk.py`, `assets/test_io_guard.py`, `assets/test_stack_node.py` —
  their stdlib test suites.
- `eval/run_eval.py` — deterministic outcome eval (see the repo's `docs/eval-standard.md`).
