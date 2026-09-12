# test-safety-net

Writes real, proven-failing-before-passing tests into a repo that has none, so an agent (or a
person) can change it without flying blind. Python, node/TypeScript and Go, all end to end. Not a
correctness audit — it pins current behaviour even where it looks wrong, and reports the suspicion
rather than silently blessing it — and never a coverage-percentage chaser.

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

Only filesystem, clock, randomness, and environment variables are controlled automatically (on
Go, randomness is not: `rand.Seed` has been a no-op since Go 1.24) — database and HTTP are declined
to Tier 3 by default and are only ever pinned at Tier 2 with a recorded justification (an in-memory
database, or `mockstar-mock` for HTTP), never silently.

Static triage is a **filter**, not the enforcement — dynamic dispatch, in Python, JavaScript and
Go alike (reflection, interface methods, function values), means source analysis cannot decide what
a unit really touches. The invariant
"never write a test that performs real I/O" is enforced at runtime instead, by a tier-aware guard
per stack, loaded on the single-test invocation rather than written into the repo:

- [`assets/io_guard.py`](assets/io_guard.py) — a **pytest plugin** loaded with `-p`, ahead of
  collection. It patches the lowest layer Python exposes — `builtins.open`, `io.open`, the `os`
  primitives including the directory-and-metadata family no fd-level patch can see,
  `socket.socket`, `subprocess.Popen` and the DB entry points already imported.
- [`assets/io_guard.js`](assets/io_guard.js) — **preloaded with `node --require`**, so it arms
  before the test file, and therefore before the unit, is loaded. It patches every own function of
  `node:fs`, of `node:fs/promises` (a different set from the callback face), of `node:net`,
  `node:http`, `node:child_process` and the rest, each behind a `Proxy` so a patched class stays a
  class; database drivers are patched the moment the repo requires one. Because node reads every
  `.js` it loads through `fs.readFileSync`, the block decision is scoped by **call provenance** —
  blocking on the name alone kills a test that touches no filesystem at all, inside the module
  loader.
- [`assets/io_guard_go.py`](assets/io_guard_go.py) — a wrapper around **`go test -overlay`** that
  compiles hooks into the standard library for the one proof run, so the whole test binary, every
  `init()` included, runs guarded. It hooks every classified `syscall` function and the clock,
  randomness, database and network entry points, decides each call by **call provenance**
  (`runtime.Callers`), and ends the process on a trip, so `recover()` cannot swallow one.

All three raise their own violation, so a guard trip (the classification is wrong) is never
mistaken for an assertion failure (the captured value is wrong). Full mechanism, coverage table and
residuals in [`references/triage.md`](references/triage.md) for python and
[`references/stacks.md`](references/stacks.md) for node and go — including node's one rule with no Python
equivalent: when a violation lands after its test resolved, `node --test` blames the wrong entry, so
**the exit status is the only trustworthy signal on that stack.**

Tiers 3 and 4 are output, not failure — a ranked "here's what blocks testing and the smallest fix"
list is the handoff to `clean-code`, and is often worth more to a human than the tests themselves.

## Install

```bash
npx skills add dhanesh/agent-skills --skill test-safety-net
```

No further setup: the bundled ranker is offline, stdlib-only python3 (git CLI needed only for the
churn signal). Three stacks are complete — **Python** (pytest, falling back to `unittest`),
**node/TypeScript** (node 18+, `node --test`, no dependency added) and **Go** (go 1.26, `go test`,
darwin and linux, no dependency added). rust is not covered: it is not registered, so the ranker
says so on stderr and returns an empty plan rather than guessing (see
[`references/stacks.md`](references/stacks.md)). Node's optional precise discovery drives a
`typescript` the repo already ships and never downloads one; Go's runs this skill's own `go/ast`
helper and never downloads a toolchain.

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
- `references/triage.md` — the four-tier triage, boundary controls, and python's runtime guard:
  what it patches, how a trip is signalled, and its seven residuals.
- `references/stacks.md` — per-stack facts (find units / where tests go / which framework / run one
  test), and the node and go halves of the guard story: their patch tables, their residuals, node's
  exit-status rule and go's `-overlay` mechanism.
- `references/parameters.md` — `rank_risk.py`'s CLI flags and JSON output shape.
- `assets/rank_risk.py` — the stack-agnostic ranker: churn, approximate blast radius,
  scoring, the CLI and the JSON shape, plus the stack registry everything else hangs off.
- `assets/stack_python.py` — the Python stack: unit discovery and testability triage.
- `assets/stack_node.py` — the node/TypeScript stack: heuristic discovery of exported
  units (plus the optional precise path through a `typescript` the repo already ships),
  the naming, lexing and import-grammar answers JS gives, and node's own I/O marker
  tables and triage.
- `assets/stack_go.py` — the Go stack: heuristic and `go/ast` discovery of exported functions
  and methods, package-scoped naming and coverage, the 272-name `syscall` table both layers share,
  and Go's I/O marker tables and triage.
- `assets/stack_common.py` — the file helpers and the manifest-evidence rule the
  ranker and every stack share.
- `assets/io_guard.py` — python's tier-aware runtime I/O guard, loaded as a pytest plugin via `-p`.
- `assets/io_guard.js` — node's, preloaded with `node --require`; dependency-free CommonJS.
- `assets/io_guard_go.py` — go's, a stdlib-python wrapper around `go test -overlay`.
- `assets/test_io_guard_node.sh` — the node guard's shell suite, whose first assertion extracts the
  documented invocation from every document that prints it and runs it verbatim, in both
  directions.
- `assets/test_io_guard_go.py` — the go guard's suite, which does the same for its own
  invocation and proves every group, the tier-2 controls and the exit contract.
- `assets/test_rank_risk.py`, `assets/test_io_guard.py`, `assets/test_stack_node.py`,
  `assets/test_stack_go.py` — their stdlib test suites.
- `eval/run_eval.py` — deterministic outcome eval (see the repo's `docs/eval-standard.md`).
