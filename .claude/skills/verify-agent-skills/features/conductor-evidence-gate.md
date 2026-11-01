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
and `--edit <path in the run repo>=<file>` for what the task commits. Each `--feature`
file of the fixture holds a `## Contract` that defines C1 and C2 only.

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

A clause counts at a tier only when the `## Contract` section of its feature file at
the verified head defines it (C2). The conductor checks this before it reads a record.
Build a fixture whose task names `notes-create:C9`, which the fixture's contract does
not define:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name tier-undefined \
  --feature notes-create=a.txt \
  --task-json '{"min_tier": "runtime", "clauses": ["notes-create:C9"]}'
```

Record the doctor and the runtime `evidence.json` as in the first recipe. Then
`evidence` prints `EVIDENCE: T1 reject tier-below-required notes-create:C9` with exit 3,
although the runtime evidence passed. A property proof recorded for C9 does not count
either. A feature file with no `## Contract` section defines no clause.

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
PATH=<bin_dir>:$PATH python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name assure-finish -- \
  python3 factory-conductor/assets/conductor.py finish --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name assure-status -- \
  python3 factory-conductor/assets/conductor.py status --root <root>
```

Expect `EVIDENCE: T1 pass <sha>` and `MERGE: T1 <commit>`. `finish` re-runs each
proof with `pytest`, so `<bin_dir>` must be first on its PATH (see the next recipe).
`finish` prints `INTEGRATION: pass`, `FINISH: <envelope path>`, `GATE: ASK` and
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

### Break a proof in a later task and read the red finish

In a tiered plan, once every verify run of the integration passes, `finish` re-runs the
source proof of each proved, bounded or property clause on the integration head. The
source is the record that the proven task with the strongest tier stored at its evidence
pass (`clause_records` in its evidence record in `state.json`). `finish` runs the
record's stored argv at the root of a clean checkout of that head, with its stored
timeout, and writes no record. For bounded and property, it also applies the record's
mutant patch to a second checkout and runs the argv there.

Build the `tier-both` fixture of the property recipe and merge T1 as above, but do not
finish. `init` left the run branch checked out in `<root>`. Commit a second change on
it that sets `target.txt` to `caught`, a value that `tool.py` fails on. Then finish with
`<bin_dir>` first on PATH:

```sh
printf 'caught\n' > <root>/target.txt && git -C <root> commit -qam "break the proof"
PATH=<bin_dir>:$PATH python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name proof-red -- \
  python3 factory-conductor/assets/conductor.py finish --root <root>
```

Expect `INTEGRATION: fail T1 proof notes-create:C1` and
`INTEGRATION: fail T1 proof notes-create:C2`, then `FINISH: <envelope path>` and
`STOP: integration_red`, with exit 3. Nothing is pushed. In the envelope,
`predicate.payload.integration.proofs` holds one
`{task, clause, tier, tool, argv, outcome, mutant_rechecked}` item per clause, and
`passed` is false. Other outcomes, each of which prints
`INTEGRATION: <outcome> T1 proof <clause>` before `INTEGRATION: pass <sha>`, and lets the
run pass with that clause at `runtime`:

- `flaky`: the head run fails once, then passes.
- `unavailable`: the tool cannot start (for example, `<bin_dir>` is not on PATH).
- `stale-mutant`: the mutant patch does not apply to the head (`target.txt` no longer
  holds `good`).
- `uncaught`: the mutant run does not fail with the tool's failure signature, twice.

`factory-conductor/assets/test_conductor_finish_proofs.py` drives each outcome with a
second task. A plan with no tier field re-runs no proof, and its `integration` has no
`proofs` key.

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

### Add a Rust escape hatch and read the repair

In a tiered plan, `evidence` reads the diff from the run base to the task's head,
limited to `.rs` files. It does this for every task, after the contract check and
before the other evidence checks. Write `<lib.rs>` with `#[verifier::external_body]` on
its first line, and build a tiered run whose task commits it:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name hatch \
  --feature notes-create=a.txt \
  --task-json '{"min_tier": "runtime", "clauses": ["notes-create:C1"]}' \
  --edit src/lib.rs=<lib.rs>
```

Record the doctor and the runtime `evidence.json` as in the first recipe. Then:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name hatch-reject -- \
  python3 factory-conductor/assets/conductor.py evidence T1 --verifier fixture-verifier --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name hatch-resume -- \
  python3 factory-conductor/assets/conductor.py resume --root <root>
```

Expect `EVIDENCE: T1 reject escape-hatch src/lib.rs:1` with exit 3. This reject is a
repair, so `resume` prints `NEXT: T1 dispatch-repair evidence`. Other cases:

- A file that adds `kani::assume(` (or `prop_assume(`) instead: `EVIDENCE: T1 pass <sha>`.
- The same `external_body` file without `--task-json`: the plan has no `min_tier`, so
  `evidence` prints `EVIDENCE: T1 pass <sha>`.
- Hits in two files: the reject names the first one in git's path order.
- A diff that git cannot show: `EVIDENCE: T1 reject evidence-diff-unreadable` and
  `PARK: T1 evidence-diff-unreadable`. `factory-conductor/assets/test_conductor_escape_hatch.py`
  covers this case.

The rule is in the spec's Terms. The vectors in
`verification-skill-forge/assets/escape_hatch_vectors.json` hold its cases, and the
conductor's copy and forge's copy both pass them.

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
