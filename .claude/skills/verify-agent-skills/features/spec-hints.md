# spec-hints: lint a spec and compile it into a task plan

- id: spec-hints
- proven: a6fe65645bf97894f1bd8e7d8dbab9f5042722f2
- anchors: spec-first-planning/assets/spec_lint.py, spec-first-planning/assets/spec_to_tasks.py, spec-first-planning/references/spec-format.md, spec-first-planning/assets/schemas/task-plan.v1.json

## What it is

A planner runs `spec_lint.py` on a spec to check that every requirement is testable and
traceable, then `spec_to_tasks.py --json` to compile it into a task plan whose tasks carry
the requirement's hints as task fields. The field names differ from the hint names:

| Hint on the requirement | Task field |
|---|---|
| `[where: ...]` | `where` (list of paths) |
| `[feature: ...]` | `features` (list of feature ids) |
| `[proof: ...]` | `predicate` (string) |
| `[after: R<n>, ...]` or `[parallel-safe]` | `independence` (list of task ids, or the string `parallel-safe`) |
| `[tier: ...]`, `[contract: ...]` (only when the spec uses them) | `min_tier`, `clauses` |

## How to reach it

`python3 spec-first-planning/assets/spec_lint.py [--converged|--unattended] <spec.md>` and
`python3 spec-first-planning/assets/spec_to_tasks.py <spec.md> [--json|--waves|--envelope <root>]`.

## Drive it

The commands below drive the repository's proof-tiers spec. To prove the feature on a
different spec, put that spec's path in place of the `docs/factory/...` path. Run from
`$WT`, because `drive.py capture` runs the command from `$WT` and the paths are relative:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sh-lint -- \
  python3 spec-first-planning/assets/spec_lint.py --unattended docs/factory/2026-10-22-proof-tiered-verification-spec.md
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sh-plan -- \
  python3 spec-first-planning/assets/spec_to_tasks.py --json docs/factory/2026-10-22-proof-tiered-verification-spec.md
```

Expect exit 0 from both: `LINT_RESULT: PASS (... mode=unattended)`, then a JSON object
with `tasks`, `coverage` and `uncovered`. Every task carries `features`, `predicate` and
`independence`, `coverage` maps every requirement to a task, and `uncovered` is `[]`. No
title keeps a hint (`[where:`, `[feature:`, `[proof:`, `[after:`, `[parallel-safe]`).
A title can still contain square brackets that are part of the requirement text (in the
proof-tiers spec, T1 and T9 do), so check for the hint prefixes, not for any `[`.

### The lone-tier case

Lint refuses a `[tier: ...]` hint with no `[contract: ...]` hint, and a clause number
of 0 or with a leading zero. To drive it, copy the proof-tiers spec to a scratch file
under the instance directory, then add `[tier: property]` to the end of the R2 line.
Do not edit the tracked spec. Then lint the copy:

```sh
cp docs/factory/2026-10-22-proof-tiered-verification-spec.md "$WT/.verify-run/$INSTANCE/lone-tier.md"
python3 - "$WT/.verify-run/$INSTANCE/lone-tier.md" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = re.sub(r"(?m)^(- R2: .*)$", r"\1 [tier: property]", s, count=1)
open(p, "w", encoding="utf-8").write(s)
PY
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name sh-lone-tier -- \
  python3 spec-first-planning/assets/spec_lint.py --unattended .verify-run/$INSTANCE/lone-tier.md
```

Expect exit 1, the line `FAIL: R2 has a [tier: ...] hint but no [contract: ...] hint`
and `LINT_RESULT: FAIL`. Add ` [contract: notes-create:C0]` after the tier and lint
again: expect exit 1 and `FAIL: R2 [contract: ...] has a malformed entry
'notes-create:C0'`. Change `C0` to `C1`: expect exit 0 and `LINT_RESULT: PASS`. If R2
already has a tier or contract hint, pick a requirement that has neither.

## Proof

The lint capture's result line and the plan capture's task fields. Neither command writes
a tracked or untracked project file; `--envelope <root>` is the one form that writes,
under `<root>/.skill-contract/envelopes/`. Python can write ignored `__pycache__/` files.

Do not use `git status --short` alone as the side-effect proof. An envelope from an
earlier `--envelope` run, or a change to `.git/info/exclude`, changes its output without
any action from these commands. Instead, touch a marker file, run both commands again,
and list the files that are newer than the marker:

```sh
touch "$WT/.verify-run/$INSTANCE/marker"
python3 spec-first-planning/assets/spec_lint.py --unattended docs/factory/2026-10-22-proof-tiered-verification-spec.md
python3 spec-first-planning/assets/spec_to_tasks.py --json docs/factory/2026-10-22-proof-tiered-verification-spec.md >/dev/null
find "$WT" -path "$WT/.git" -prune -o -newer "$WT/.verify-run/$INSTANCE/marker" -print \
  | grep -v "/.verify-run/" > "$WT/.verify-run/$INSTANCE/newer-files.txt"
```

Expect only `__pycache__` entries. Give `newer-files.txt` to `record` as an extra
`--artifact` (the flag can be repeated), together with both captures.

## Gotchas

- `spec_to_tasks.py` prints `TASKS_RESULT:` and exits non-zero when a requirement has no
  criterion; read the coverage lines, not just the exit code.
- A spec with no new hints must compile byte-identically before and after a change (O1).
