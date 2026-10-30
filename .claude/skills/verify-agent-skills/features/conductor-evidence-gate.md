# conductor-evidence-gate: accept or reject a task's evidence before merge

- id: conductor-evidence-gate
- proven: 9ebad708af9a37fdb0b81d0d0334f7de37dcc8f3
- anchors: factory-conductor/assets/conductor.py, factory-conductor/assets/schemas/run-result.v1.json

## Contract

- C1 [pre]: a proof record counts toward a clause's tier only when its stored argv is a runner of its stored tool
- C2 [pre]: a clause counts toward a tier only when the `## Contract` section of its feature file at the verified head defines it
- C3 [post]: in a tiered plan, a task whose diff from the run base adds a Rust escape hatch is rejected with `escape-hatch <path>:<line>` and sent back as a repair
- C4 [post]: in a tiered plan, `finish` re-runs the source proof of each bounded, property or proved clause on the integration head, and a proof that fails twice turns the run red
- C5 [post]: each `ASSURANCE:` line and each envelope `assurance` item names the integration head on which the proofs were re-run

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

### Record a property proof and read the tier reject

A tiered task merges only when each clause in its `clauses` has a proof record at or
above its `min_tier`. The check runs after every other evidence check passes. First get
a fake allowlisted tool (it acts as `hypothesis`, so it needs no dependency) and a mutant
that the tool catches:

```sh
python3 "$SKILL/scripts/drive.py" fixture proof-tool --instance "$INSTANCE" --name tool
```

Expect exit 0 and JSON with `tool_py`, `target_txt`, `mutant_diff` and `bin_dir`.
`bin_dir` holds `pytest`, a PATH shim that runs `tool.py`: the recorder runs only a
runner of `--tool`, so `--cmd "python3 tool.py"` is refused. Build a gated run
whose task needs `property` on C1 and C2, and commit the tool and its target with the
task:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name tier \
  --feature notes-create=a.txt \
  --task-json '{"min_tier": "property", "clauses": ["notes-create:C1", "notes-create:C2"]}' \
  --edit tool.py=<tool_py> --edit target.txt=<target_txt>
```

Record the doctor and the runtime `evidence.json` as in the first recipe. Then record a
property proof for C1 only:

```sh
PATH=<bin_dir>:$PATH VERIFY_EVIDENCE_DIR=<root>/.verify \
  python3 verification-skill-forge/assets/verify_evidence.py record \
  --instance fx --worktree <worktree> --feature notes-create --verifier fixture-verifier \
  --tier property --tool hypothesis --clauses C1 --cmd pytest \
  --mutant-patch <mutant_diff> --mutant-author verifier
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tier-reject -- \
  python3 factory-conductor/assets/conductor.py evidence T1 --verifier fixture-verifier --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tier-resume -- \
  python3 factory-conductor/assets/conductor.py resume --root <root>
```

Expect `PROOF: property <path>` from the recorder. Expect
`EVIDENCE: T1 reject tier-below-required notes-create:C2` with exit 3: C2 has only
runtime evidence. This reject is a repair, so `resume` prints
`NEXT: T1 dispatch-repair evidence`, and `conductor verify T1` refuses the same commit.

In a second fixture (`--name tier-both`), record C1 and C2 the same way. Then `evidence`
prints `EVIDENCE: T1 pass <sha>` with exit 0. A record does not count when the owner
(`writer`) recorded it, or when one of its artifacts was edited (for example
`proof-property-hypothesis-C2.stdout`). Then the reject names that clause again.

When the pinned plan holds a tier field that cannot be read (`clauses` as `[5]`, `[null]`
or `"x"`, or `min_tier` as null), `evidence` prints
`EVIDENCE: T1 reject tier-field-unreadable <min_tier|clauses>` and `PARK: T1
tier-field-unreadable`, with exit 3. `init` refuses such a plan, so this fixture cannot
build one. `factory-conductor/assets/test_conductor_tiers.py` builds it with init's check
turned off.

### Drive a gated run through finish and read the ASSURANCE lines

The `gated-run` fixture stops after review. To finish the run, merge the task and run
`finish` yourself. The fixture's grant asks for push and PR, so `finish` writes the
run-result envelope and pushes nothing.

Build the `tier-both` fixture of the recipe above, and record the doctor, the runtime
`evidence.json` and a property proof for C1 and for C2. Then:

```sh
python3 factory-conductor/assets/conductor.py evidence T1 --verifier fixture-verifier --root <root>
python3 factory-conductor/assets/conductor.py merge T1 --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name assure-finish -- \
  python3 factory-conductor/assets/conductor.py finish --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name assure-status -- \
  python3 factory-conductor/assets/conductor.py status --root <root>
```

Expect `EVIDENCE: T1 pass <sha>` and `MERGE: T1 <commit>`. `finish` prints
`INTEGRATION: pass`, `FINISH: <envelope path>`, `GATE: ASK` and
`REMOTE: pending push pr`, with exit 3. `status` prints
`ASSURANCE: notes-create property C1,C2` after its last `STATUS:` line. Open the envelope:
`predicate.payload.assurance` is
`[{"feature": "notes-create", "tier": "property", "clauses": ["C1", "C2"]}]`, the same
data as the line.

A clause's tier is the strongest tier that a merged task recorded for it at its evidence
pass. The feature's tier is its weakest clause's tier. The line names the clauses at that
tier, in numeric order (C2 before C10). Other cases:

- A `proved` proof (tool `verus`) for C1 and a property proof for C2:
  `ASSURANCE: notes-create property C2`.
- `min_tier` `runtime` and no proof record: `ASSURANCE: notes-create runtime C1,C2`.
- Before the merge, or when the task parks: the tier is `none`.
- No `--task-json`: `status` prints no `ASSURANCE:` line, and the envelope has no
  `assurance` key.

The evidence record of T1 in `<root>/.skill-contract/runs/<run-id>/state.json` holds
`clause_tiers`, the tier each clause met at pass.

### Edit a contract and read the park

In a tiered plan, `evidence` compares the `## Contract` section of each feature file
(not `README.md`) at the task's head with the run base. The run base is
`git merge-base <run branch> <head>`. It does this for every task, before the other
evidence checks. Write two feature files: `<c1>` with a `## Contract` that holds C1 and
C2, and `<c1-edited>`, the same file with other text for C1. Build a tiered run that
starts from `<c1>` and whose task commits `<c1-edited>`:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name contract \
  --feature notes-create=a.txt --feature-file notes-create=<c1> \
  --task-json '{"min_tier": "runtime", "clauses": ["notes-create:C1"]}' \
  --edit .claude/skills/verify-app/features/notes-create.md=<c1-edited>
```

Record the doctor and the runtime `evidence.json` as in the first recipe, so every
record passes. Then ask the gate:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name contract-park -- \
  python3 factory-conductor/assets/conductor.py evidence T1 --verifier fixture-verifier --root <root>
```

Expect `EVIDENCE: T1 reject contract-changed notes-create` and `PARK: T1 contract-changed`,
with exit 3. `conductor status --root <root>` shows T1 as parked. A feature file that
is on one side only is also a change, with or without a contract. So is a section on
one side only. Trailing spaces and text
outside the section are not. Run the same fixture without `--task-json`: the plan has
no `min_tier`, so `evidence` prints `EVIDENCE: T1 pass <sha>`.
`factory-conductor/assets/test_conductor_contract.py` also covers a task with no tier
field in a tiered plan: it parks the same way.

## Proof

The two captures, plus the run state: `conductor status --root <root>` shows T1's evidence
verdict, and `<root>/.skill-contract/runs/<run-id>/autonomy-log.jsonl` gains an `evidence`
event for each call (the side effect).

For the contract: C1 holds when a property record whose stored argv is not a runner of its
tool leaves its clause below the required tier. C2 holds when a clause that the contract
does not define is rejected as `tier-below-required`. C3 holds when a task that adds
`#[verifier::external_body]` gets `EVIDENCE: <task> reject escape-hatch <path>:<line>`.
C4 holds when a proof broken by a later task gives `STOP: integration_red` at `finish`.
C5 holds when the `ASSURANCE:` line ends with `@<first 12 characters of the integration head>`.

## Gotchas

- `<root>`, `<worktree>` and `<sha>` come from the fixture's JSON; copy them, do not guess.
- Set `VERIFY_EVIDENCE_DIR` on the fixture commands only: your own records for this
  feature belong in the evidence directory the conductor brief named.
- `fixture-verifier` and `writer` are the fixture's identities, not yours.
