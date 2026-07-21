# Spec format, lint rules, and plan schema

The mechanical contract shared by `assets/spec_lint.py` and
`assets/spec_to_tasks.py`. The template that satisfies it is
`references/spec-template.md`.

## Spec grammar

A spec is one markdown file:

- **Title**: an H1, conventionally `# Spec: <name>` (the `Spec:` prefix is
  stripped when the plan inherits the title).
- **Sections**: H2 headings, matched case-insensitively. Required, in any
  order: `Problem`, `Users`, `Goals`, `Non-goals`, `Requirements`,
  `Acceptance criteria`, `Open questions`. Extra sections are allowed and
  ignored by the tooling.
- **Requirement bullets** (in `## Requirements`): `- R<n>: <statement>`.
  Ids run R1..Rn in document order with no gaps or duplicates. Each
  statement is a single testable obligation containing `must` or `shall`.
  Optional trailing hint `[where: path/or/area]` — lifted into the derived
  task's Where field, stripped from its title.
- **Criterion bullets** (in `## Acceptance criteria`): `- R<n>: <check>`.
  At least one per requirement; a criterion may mention several ids
  (`covers R1 and R2`), and every id it mentions must exist. Write each
  check as something runnable: a command plus expected exit code/output, or
  an observation an outside party could make.
- **Open questions**: the section must exist; its list may be empty.

## Lint rules (`assets/spec_lint.py <spec.md>`)

| # | Rule | Failure it catches |
|---|------|--------------------|
| 1 | Required sections present and non-empty (`Open questions` may be empty) | spec skipped the thinking a section forces |
| 2 | Ids exactly R1..Rn, ordered, no gaps/duplicates | requirements silently dropped or forked |
| 3 | Every requirement contains `must`/`shall` | a wish posing as a requirement |
| 4 | Vague term with no metric in the same statement | unfalsifiable adjective ("fast", "robust", "user-friendly", "simple", "reliable", "scalable", "efficient", "seamless", "responsive", ...). A digit, `%`, `<=`, `>=`, `≤`, or `≥` in the statement licenses the word |
| 5 | Every requirement referenced by ≥1 criterion; no criterion references an unknown id | a requirement nothing can prove; a check proving nothing |

Output: one `FAIL: ...` line per issue, final `LINT_RESULT: PASS` or
`LINT_RESULT: FAIL (n issue(s))`. Exit 0 iff clean; 2 on unreadable input.

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
     "verify": "criterion 1; criterion 2", "where": "web/reports/"}
  ],
  "coverage": {"R1": ["T1"], "R2": []},
  "uncovered": ["R2"]
}
```

`where` appears only when the requirement carried a `[where: ...]` hint.
Deterministic: same spec in, byte-identical plan out.

Exit codes: `0` total coverage; `1` any requirement uncovered (a plan with
a hole is not a plan); `2` unreadable input or no `R<n>:` bullets at all.
