# conductor-evidence-gate: accept or reject a task's evidence before merge

- id: conductor-evidence-gate
- proven: 9ebad708af9a37fdb0b81d0d0334f7de37dcc8f3
- anchors: factory-conductor/assets/conductor.py, factory-conductor/assets/schemas/run-result.v1.json

## What it is

Under a plan with a `verification` block, factory-conductor holds each task's merge until
`conductor evidence <task> --verifier <id>` passes: SHA-bound evidence for every touched
feature, recorded by a verifier who is not the writer, with a green doctor. It prints
`EVIDENCE: <task> pass <sha>` (exit 0) or `EVIDENCE: <task> reject <reason> ...` (exit 3).
`conductor status` reports the run.

## How to reach it

`python3 factory-conductor/assets/conductor.py <evidence|status|merge|...> --root <run root>`.

## Drive it

Build a gated run whose task T1 is verified and reviewed and waits for evidence:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name gate --feature notes-create=a.txt
```

Expect exit 0 and JSON with `root`, `worktree` and `sha`. Record into the fixture's own
evidence directory, as its verifier, then ask the gate:

```sh
VERIFY_EVIDENCE_DIR=<root>/.verify python3 verification-skill-forge/assets/verify_evidence.py doctor \
  --instance fx --worktree <worktree> --ok --check cli=pass --verifier fixture-verifier
VERIFY_EVIDENCE_DIR=<root>/.verify python3 verification-skill-forge/assets/verify_evidence.py record \
  --instance fx --worktree <worktree> --feature notes-create --verifier fixture-verifier --result pass \
  --action "fixture" --observed "fixture" --side-effect "none: fixture" --artifact <any file>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gate-pass -- \
  python3 factory-conductor/assets/conductor.py evidence T1 --verifier fixture-verifier --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gate-writer -- \
  python3 factory-conductor/assets/conductor.py evidence T1 --verifier writer --root <root>
```

Expect `EVIDENCE: T1 pass <sha>` with exit 0, then `EVIDENCE: T1 reject writer-is-verifier`
with exit 3 (the fixture's owner is `writer`).

For contract and tier cases, pass the feature file and the task fields to the fixture:
`--feature-file notes-create=<file with a ## Contract>`, `--task-json '{"min_tier": "property", "clauses": ["notes-create:C1"]}'`,
and `--edit <path in the run repo>=<file>` for what the task commits.

### Drive an init refusal of tier fields

`conductor init` refuses a plan whose tier fields are malformed. It does this before it
checks verify commands and the grant. Give the fixture a bad `--task-json`. The fixture
stops at `init` and prints the refusal:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name tier-bad \
  --feature notes-create=a.txt --task-json '{"min_tier": "fast", "clauses": ["notes-create:C1"]}'
```

Expect exit 1 and JSON with `error: "conductor init exited 2"`. The last item of `steps`
has `"exit": 2`, and its `stderr` holds `FAIL: task T1 min_tier: ...`. Other cases:

- `{"min_tier": null, ...}` or `{"min_tier": "Property", ...}`: `FAIL: task T1 min_tier:`.
- `clauses` as `null`, a string, `[]`, `[5]`, `[null]`, a duplicate, `notes-create:C0`,
  or a clause whose feature the task does not name (`notes-list:C1`): `FAIL: task T1 clauses:`.
- One key alone: the FAIL line names the key that is missing. For example,
  `{"min_tier": "property"}` gives `FAIL: task T1 clauses: missing; ...`.
- No evidence gate: add `--plan-json '{"verification": null}'` with valid tier fields.
  The FAIL lines say `the plan declares no verification block`.

Then check that the refusal wrote nothing. `ls <root>/.skill-contract/runs` fails (no run
directory), and `git -C <root> branch` lists only `main` and `factory/base` (no new branch).
With `{"min_tier": "property", "clauses": ["notes-create:C1"]}`, or with no `--task-json`,
the fixture exits 0: the run starts.

## Proof

The two captures, plus the run state: `conductor status --root <root>` shows T1's evidence
verdict, and `<root>/.skill-contract/runs/<run-id>/autonomy-log.jsonl` gains an `evidence`
event for each call (the side effect).

## Gotchas

- `<root>`, `<worktree>` and `<sha>` come from the fixture's JSON; copy them, do not guess.
- Set `VERIFY_EVIDENCE_DIR` on the fixture commands only: your own records for this
  feature belong in the evidence directory the conductor brief named.
- `fixture-verifier` and `writer` are the fixture's identities, not yours.
