# evidence-recorder: record doctor checks and SHA-bound evidence

- id: evidence-recorder
- proven: no
- anchors: verification-skill-forge/assets/verify_evidence.py

## Contract

- C1 [pre]: `record --tier` runs `--cmd` only when its argv is a runner of `--tool` in the recorder's literal runner table, after at most one wrapper is stripped and with no `/` in argv[0]; for any other argv it exits 2, runs nothing and writes nothing
- C2 [pre]: `record --tier` refuses a worktree whose tracked files differ from the committed head, and runs the head proof and the mutant proof each in its own clean checkout of that head

## What it is

A verifier runs `verify_evidence.py record` to store proof that a feature worked on a
commit: the action, what it observed, the side effects and the captured files, each pinned
by sha256, under `<evidence dir>/<instance>/<feature>/<sha>/`. `doctor` stores the
instance's health check the same way. The recorder reads the commit from git, never from a
flag, and refuses a record that lacks a side effect or an artifact.

## How to reach it

`python3 verification-skill-forge/assets/verify_evidence.py <port|claim|release|doctor|record> ...`.
Every generated verify skill carries a byte-identical copy in its `scripts/`.

## Drive it

Drive the forge's copy (the code under test) against a fixture repo, with the evidence
directory pointed into the fixture so your own records stay separate:

```sh
python3 "$SKILL/scripts/drive.py" fixture verify-skill --instance "$INSTANCE" --name er
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name er-record -- \
  env VERIFY_EVIDENCE_DIR="$WT/.verify-run/$INSTANCE/er/.verify" \
  python3 verification-skill-forge/assets/verify_evidence.py record --instance fx \
  --worktree "$WT/.verify-run/$INSTANCE/er" --feature notes-create --verifier fixture-verifier \
  --result pass --action "drove notes-create" --observed "201" --side-effect "none: fixture" \
  --artifact "$WT/.verify-run/$INSTANCE/er/app.py"
```

Expect exit 0 and `EVIDENCE: pass <path>/fx/notes-create/<fixture head>/evidence.json`.

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name er-refuse -- \
  env VERIFY_EVIDENCE_DIR="$WT/.verify-run/$INSTANCE/er/.verify" \
  python3 verification-skill-forge/assets/verify_evidence.py record --instance fx \
  --worktree "$WT/.verify-run/$INSTANCE/er" --feature notes-list --verifier fixture-verifier \
  --result pass --action a --observed o --artifact "$WT/.verify-run/$INSTANCE/er/app.py"
```

Expect a non-zero exit and an error saying `--side-effect` is required.

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name er-runner -- \
  env VERIFY_EVIDENCE_DIR="$WT/.verify-run/$INSTANCE/er/.verify" \
  python3 verification-skill-forge/assets/verify_evidence.py record --instance fx \
  --worktree "$WT/.verify-run/$INSTANCE/er" --feature notes-create --verifier fixture-verifier \
  --tier proved --tool verus --clauses C1 --cmd true
```

Expect exit 2 and `--cmd runs true, which is not a runner of --tool verus (verus, cargo
verus)`. `true` would pass, but the recorder does not run it. To record a proof without
the real tool, put a script with the runner's name first on PATH, as
`drive.py fixture proof-tool` does (`bin_dir`).

## Proof

The written `evidence.json` names the fixture head as `sha`, and its `artifacts[].sha256`
equals `shasum -a 256` of the stored copy (the side effect: one new record directory). The
refused record leaves no `notes-list` directory under the fixture's `.verify/`.

For the contract: C1 holds when a proof record whose `--cmd` is not a runner of its
`--tool` exits 2 and leaves the evidence directory unchanged. C2 holds when a tracked edit
makes `record --tier` exit 2, and a written proof record stores `cwd` `"."` and `ran_in`
`"checkout"`.

## Gotchas

- `--worktree` decides the commit; without it the recorder uses the current directory's
  head, which is the checkout under test, not the fixture.
- `env VERIFY_EVIDENCE_DIR=...` inside the capture keeps fixture records out of the run's
  own evidence directory.
