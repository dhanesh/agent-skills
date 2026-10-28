# spec-hints: lint a spec and compile it into a task plan

- id: spec-hints
- proven: no
- anchors: spec-first-planning/assets/spec_lint.py, spec-first-planning/assets/spec_to_tasks.py, spec-first-planning/references/spec-format.md, spec-first-planning/assets/schemas/task-plan.v1.json

## What it is

A planner runs `spec_lint.py` on a spec to check that every requirement is testable and
traceable, then `spec_to_tasks.py --json` to compile it into a task plan whose tasks carry
the requirement's hints (`where`, `after`, `feature`, `proof`) as task fields.

## How to reach it

`python3 spec-first-planning/assets/spec_lint.py [--converged|--unattended] <spec.md>` and
`python3 spec-first-planning/assets/spec_to_tasks.py <spec.md> [--json|--waves|--envelope <root>]`.

## Drive it

Use the spec-first-planning eval's own clean fixture, or a spec of your own written into
the instance directory:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sh-lint -- \
  python3 spec-first-planning/assets/spec_lint.py --unattended docs/factory/2026-10-22-proof-tiered-verification-spec.md
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sh-plan -- \
  python3 spec-first-planning/assets/spec_to_tasks.py --json docs/factory/2026-10-22-proof-tiered-verification-spec.md
```

Expect exit 0 from both: `LINT_RESULT: PASS (... mode=unattended)`, then a JSON plan whose
tasks carry `features`, `predicate` and `independence`, with titles free of `[...]` hints.

## Proof

The lint capture's result line and the plan capture's task fields. Neither command writes
a file (`git status --short` unchanged); `--envelope <root>` is the one form that writes,
under `<root>/.skill-contract/envelopes/`.

## Gotchas

- `spec_to_tasks.py` prints `TASKS_RESULT:` and exits non-zero when a requirement has no
  criterion; read the coverage lines, not just the exit code.
- A spec with no new hints must compile byte-identically before and after a change (O1).
