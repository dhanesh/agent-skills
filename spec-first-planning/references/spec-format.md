# Spec format, lint rules, and plan schema

The mechanical contract shared by `assets/spec_lint.py` and
`assets/spec_to_tasks.py`. The template that satisfies it is
`references/spec-template.md`.

## Spec grammar

A spec is one markdown file:

- **Title**: an H1, conventionally `# Spec: <name>` (the `Spec:` prefix is
  stripped when the plan inherits the title).
- **Sections**: H2 headings, matched case-insensitively. Required, in any
  order: `Problem`, `Users`, `Goals`, `Non-goals`, `Constraints`,
  `Required truths`, `Requirements`, `Acceptance criteria`,
  `Open questions`. Extra sections are allowed and ignored by the tooling.
- **Constraint bullets** (in `## Constraints`, the Constrain step, always
  on): `- <ID> [<type>]: <statement>`. `<ID>` matches `(B|T|U|S|O)[0-9]+`
  (business, technical, UX, security, operational). `<type>` is
  `invariant`, `goal` or `boundary`.
- **Required-truth bullets** (in `## Required truths`, the Anchor step,
  always on): `- RT<n> [<status>]: <statement> (parent: <OUTCOME|RT<k>>;
  maps_to: <constraint ids>; reqs: <R ids>; confidence: <0..1>;
  check: <runnable check>)`. `<status>` is `SATISFIED`, `PARTIAL`,
  `NOT_SATISFIED` or `SPECIFICATION_READY`. `check:` MUST be the last
  field — everything after `check:` up to the final `)` is the check.
- **Requirement bullets** (in `## Requirements`): `- R<n>: <statement>`.
  Ids run R1..Rn in document order with no gaps or duplicates. Each
  statement is a single testable obligation containing `must` or `shall`.
  Optional trailing hint `[where: path/or/area]` — lifted into the derived
  task's Where field, stripped from its title. Optional trailing hint
  `[after: R<n>, ...]` — the requirement ids this one must follow; `R` is
  matched case-insensitively. It becomes the derived task's `depends_on`
  (mapped to the task(s) that cover each id) and is stripped from the
  title the same way `[where: ...]` is.
- **Criterion bullets** (in `## Acceptance criteria`): `- R<n>: <check>`.
  At least one per requirement; a criterion may mention several ids
  (`covers R1 and R2`), and every id it mentions must exist. Write each
  check as something runnable: a command plus expected exit code/output, or
  an observation an outside party could make. Optional trailing hint
  `[cmd: <argv>]` (keyword matched case-insensitively): the command that
  proves the criterion, split like a shell line (`shlex`) but run as an argv,
  never through a shell. It must be the last thing on the bullet, parse, be
  non-empty, and follow the skill-contract command rule (C6): start with
  `{python}` rather than `python3`, or with a bare program name; no absolute
  paths; no placeholder but a leading `{python}` or `{skill_dir:<name>}`.
  It becomes the derived verify step's `command` and is stripped from its
  text; a token inside it (`grep R9 x`) is not a requirement reference.
  Example: `- R2: row parity holds. [cmd: {python} tests/compare_export.py fixtures/report.json]`.
  Optional in light and converged mode; `--unattended` requires it on every
  criterion.
- **Open questions**: the section must exist; its list may be empty.

**Full-loop sections** (Tension + Choose; required only under `--converged` /
`--unattended`, design spec §4). How to run the loop that fills them, the
pre-mortem and the decision sweep are in `references/unattended.md`:

- **Tension bullets** (in `## Tensions`): `- TN<n> [<type>]: <text>
  (between: <ids>; status: <resolved|accepted>; strategy:
  <Prioritize|Partition|Transform|Accept|Invalidate>; decision: D<k>)`.
  `<type>` is `trade_off`, `resource_tension` or `hidden_dependency`.
  `decision` is REQUIRED when `status: accepted` or `strategy: Accept`, and
  optional otherwise. The section may instead hold the single bullet
  `- none` when no tension exists.
- **Solution-option bullets** (in `## Solution options`): `- OPT-<LETTER>:
  <text> (complexity: <Low|Medium|High>; reversibility:
  <TWO_WAY|REVERSIBLE_WITH_COST|ONE_WAY>; satisfies: <RT ids>)`. 2-4
  options. Plus one non-bullet line: `Recommended: OPT-<LETTER> —
  <rationale>`. If the recommendation breaks a tie, append `(decision:
  D<k>)`.
- **Iteration bullets** (in `## Iterations`): `- I<n>: <what changed>`,
  numbered I1..In in order, with 1 ≤ n ≤ 5.
- **Decision bullets** (in `## Decisions`, unattended mode): `- D<n>:
  <question> -> <answer> (source: <where>)`. `→` is accepted in place of
  `->`, and the answer MUST be non-empty for unattended mode.

## Lint rules (`assets/spec_lint.py <spec.md>`)

| # | Rule | Failure it catches |
|---|------|--------------------|
| 1 | Required sections present and non-empty (`Open questions` may be empty) | spec skipped the thinking a section forces |
| 2 | Ids exactly R1..Rn, ordered, no gaps/duplicates | requirements silently dropped or forked |
| 3 | Every requirement contains `must`/`shall` | a wish posing as a requirement |
| 4 | Vague term with no metric in the same statement | unfalsifiable adjective ("fast", "robust", "user-friendly", "simple", "reliable", "scalable", "efficient", "seamless", "responsive", ...). A digit, `%`, `<=`, `>=`, `≤`, or `≥` in the statement licenses the word |
| 5 | Every requirement referenced by ≥1 criterion; no criterion references an unknown id | a requirement nothing can prove; a check proving nothing |
| 5a | Every id in a requirement's `[after: ...]` hint names a known requirement; the after-hints as a whole contain no cycle (message: "after: hints have a cycle") | a task ordered after a requirement that doesn't exist, or a dependency loop no schedule can satisfy |
| 5b | A criterion's `[cmd: ...]` hint ends the criterion, parses with `shlex`, is non-empty, and passes the C6 command rule (the vendored `contract_check`) | a command no machine can run, or one that names an interpreter or an absolute path |
| 6 | Constraint grammar (`- <ID> [<type>]: ...`) and `<type>` is `invariant`/`goal`/`boundary`; no duplicate ID | a constraint the parser can't type or trace |
| 7 | Required-truth grammar, `<status>` one of the four values, `confidence` a number in `[0, 1]`, and a non-empty `check:` field | a truth with no falsifiable status, confidence, or way to verify it |
| 8 | Traceability: every constraint is named in some RT's `maps_to`; every RT names ≥1 known constraint and ≥1 known requirement (`reqs:`); every RT's `parent` is `OUTCOME` or another RT in this spec, and not itself | a constraint nobody anchors; a truth that traces to nothing |
| 9 | Every RT reaches OUTCOME through parent links (no dangling parent, no cycle) | a truth chain that never actually anchors at the outcome — including a cycle of otherwise-valid RTs (e.g. RT1 -> RT2 -> RT1) that a per-RT "parent is OUTCOME or another RT" check alone can't see |

Rules 6-9 are the Constrain + Anchor light pass (design spec §4): they run
on every spec, attended or unattended — only Tension and Choose (Solution
options, Iterations, convergence) are gated behind `--converged`.

### `--converged` (full loop: Tension + Choose + convergence)

Runs every light-pass rule above, plus:

| # | Rule | Failure it catches |
|---|------|--------------------|
| 10 | `## Tensions`, `## Solution options` and `## Iterations` are present | the full loop skipped a step |
| 11 | Tension grammar, `<type>` one of `trade_off`/`resource_tension`/`hidden_dependency`, `between:` names ≥2 known constraint ids, and every tension is `status: resolved` or cites a `decision: D<k>` (a `decision` referencing an unknown `D<k>` also fails) | a trade-off nobody actually resolved |
| 12 | Every required truth's status is `SATISFIED` or `SPECIFICATION_READY` | a spec that claims to converge with an open truth |
| 13 | Solution-option grammar, 2-4 options, each option's `satisfies:` naming at least one required truth and no unknown one (`OPT-X satisfies unknown truth RTn`), and a `Recommended: OPT-<LETTER>` line naming a known option | no real choice was made, or it's untraceable |
| 14 | The recommended option satisfies every required truth, and is the pragmatic choice: among the options that satisfy every RT, no other has a lower `(complexity rank, reversibility rank)` (Low 0 < Medium 1 < High 2; TWO_WAY 0 < REVERSIBLE_WITH_COST 1 < ONE_WAY 2). A tied alternative requires the Recommended line to cite a `(decision: D<k>)` | picking the fancier option when the simple one does the same job, with no accountability for a tie |
| 15 | `## Iterations` bullets are `I1..In` in order, no gaps/duplicates, and `n` ≤ 5 | a loop that never converges — the failing message says "iteration cap exceeded — stop and ask the user" |
| 16 | `## Open questions` has no bullets | convergence claimed while a question is still open |

Every `D<k>` referenced anywhere (a tension's `decision:` field, or the
Recommended line's `(decision: ...)`) must name a decision that exists in
`## Decisions`.

### `--unattended` (converged, plus the decision sweep)

Runs every `--converged` rule above, plus: `## Decisions` is present,
non-empty, its grammar is well-formed, and every decision's answer is
non-empty; and every acceptance criterion carries a `[cmd: ...]` hint. A
grant exists only for runs a machine can prove: factory-conductor refuses a
plan with a null verify command at `init`.

Output: one `FAIL: ...` line per issue, final `LINT_RESULT: PASS (...,
mode=<mode>)` or `LINT_RESULT: FAIL (n issue(s), mode=<mode>)`. Exit 0 iff
clean; 1 on any lint failure; 2 on bad usage or unreadable input.

CLI: `spec_lint.py [--converged|--unattended] <spec.md>` — at most one mode
flag, given before the path; omitting it runs the light pass (the default,
backward-compatible `lint(text)` mode). Programmatically,
`lint(text, mode="light"|"converged"|"unattended")`.

## Task derivation (`assets/spec_to_tasks.py <spec.md> [--json]`)

One task per requirement, in requirement order, ids T1..Tm. A task is
derivable **only** from a requirement with at least one acceptance
criterion — the criteria become the task's verify steps. Splitting a large
task into several (all pointing at the same requirement id) is a human/agent
review step, not the compiler's job.

Default output: markdown plan (per task: Satisfies / Where / Verify
checklist; then a coverage table and, when needed, an Uncovered
requirements section), followed by machine lines:

```
COVERAGE: R1 -> T1
UNCOVERED: R2
TASKS_RESULT: PASS|FAIL (k/n requirements covered by m task(s))
```

`--json` prints only:

```json
{
  "tasks": [
    {"id": "T1", "requirement_ids": ["R1"], "title": "...",
     "verify": "criterion 1; criterion 2", "where": "web/reports/",
     "depends_on": ["T0"],
     "verify_commands": [["{python}", "-m", "pytest", "-k", "rows"], null]}
  ],
  "coverage": {"R1": ["T1"], "R2": []},
  "uncovered": ["R2"]
}
```

`where` appears only when the requirement carried a `[where: ...]` hint;
`depends_on` appears only when it carried an `[after: ...]` hint that
resolved to at least one covering task; `verify_commands` (one argv or
`null` per verify step, in step order) only when at least one of its
criteria carried a `[cmd: ...]` hint. The markdown plan shows each command
after its step as ``(cmd: `...`)``. A requirement with no hints derives
a plan byte-identical to one from before `[after: ...]` existed.
Deterministic: same spec in, byte-identical plan out.

Exit codes: `0` total coverage; `1` any requirement uncovered (a plan with
a hole is not a plan); `2` unreadable input or no `R<n>:` bullets at all.

### Waves and the critical path (`--waves`)

`spec_to_tasks.py <spec.md> --waves` groups the derived tasks into waves from
their `depends_on` and prints:

```
WAVE 1: T1
WAVE 2: T2 T3
WAVE 3: T4
CRITICAL_PATH: T1 -> T2 -> T4
WAVES_RESULT: PASS (3 wave(s))
```

then exits 0. Every task in a wave is independent of the others in that wave;
wave N+1 depends on wave N only through `depends_on` edges already resolved.
Task ids sort numerically within a wave and along the critical path (`T10`
after `T2`, not before). The critical path is the longest chain of
`depends_on`; a tie is broken by the chain whose ids come first numerically,
compared element by element; with no dependencies at all it is the single
lowest-numbered task.

The wave algorithm is copied from `factory-conductor/assets/conductor.py`'s
`waves(tasks)` — the same scheduler factory-conductor uses to run a plan —
rather than imported across skills. An `[after: Rn]` hint naming an unknown
requirement, or a cycle among the hints, already fails `spec_lint.py` (rule
5a) before a plan is even derived; `--waves` still validates its own input
(a schedule error prints `ERROR: ...` to stderr and exits non-zero) since a
`depends_on` reaching it some other way — a hand-edited plan, for instance —
isn't spec_lint's to catch.

### The task-plan envelope (`--envelope <repo-root>`)

`spec_to_tasks.py <spec.md> --envelope <repo-root>` also writes the plan as a
skill-contract `task-plan/v1` envelope under
`<repo-root>/.skill-contract/envelopes/` and prints `ENVELOPE: <path>`. The
payload is the JSON plan above plus `title` and `spec`, and three optional
fields lifted from the spec when present: `constraints` (`{id, type, text}`),
`required_truths` (only when every truth is well formed) and `decisions`
(`{id, question, answer, source}`). Each task's `verify` is a list of
`{text, command}`: `command` is the criterion's `[cmd: ...]` argv, or `null`
when it has none. `task-plan/v1` stays v1 because the
fields are additive. The schema is `assets/schemas/task-plan.v1.json`.

In unattended mode this envelope is the plan a grant pins:
`assets/write_grant.py` takes it as `--plan`, refuses a plan that was
derived from a different spec or is stale, and writes an
`autonomy-grant/v1` envelope beside it (see `references/unattended.md`).
