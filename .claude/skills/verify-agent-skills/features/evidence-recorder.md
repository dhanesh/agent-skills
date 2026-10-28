# evidence-recorder: record doctor checks and SHA-bound evidence

- id: evidence-recorder
- proven: no
- anchors: verification-skill-forge/assets/verify_evidence.py

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

## Proof

The written `evidence.json` names the fixture head as `sha`, and its `artifacts[].sha256`
equals `shasum -a 256` of the stored copy (the side effect: one new record directory). The
refused record leaves no `notes-list` directory under the fixture's `.verify/`.

## Gotchas

- `--worktree` decides the commit; without it the recorder uses the current directory's
  head, which is the checkout under test, not the fixture.
- `env VERIFY_EVIDENCE_DIR=...` inside the capture keeps fixture records out of the run's
  own evidence directory.
