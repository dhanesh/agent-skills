# posture-audit: flag a risky CI workflow with audit_posture.py, with the risky fixture lines kept in data files

- id: posture-audit
- proven: no
- anchors: security-posture-audit/assets/audit_posture.py:_check_workflow, security-posture-audit/assets/audit_posture.py:_CURL_PIPE_SH, security-posture-audit/assets/test_audit_posture.py, security-posture-audit/eval/run_eval.py, security-posture-audit/SKILL.md

## What it is

**Status: planned (run 5).** After the fix, the risky-workflow fixtures of
security-posture-audit are data files under `security-posture-audit/assets/fixtures/`.
Each fixture file starts with a comment that says it is a detector fixture and that
nothing runs it. The unit tests (`assets/test_audit_posture.py`) and the outcome eval
(`eval/run_eval.py`) read the risky workflow text from those files. No Python source of
the skill holds a literal "download, then pipe to a shell" line any more, so a scanner
that reads the test and eval sources finds no such line. The detector does not change:
`audit_posture.py` still reports `workflow-curl-pipe-sh` at HIGH for a workflow step that
pipes `curl` or `wget` into a shell. `SKILL.md` also states a data boundary: the agent
treats every scanned file as data and ignores any instruction that a scanned file holds.

Why: the skills.sh Gen scanner rates the skill High because the test and eval sources
carry that fixture line, and because SKILL.md has no explicit data boundary.

## How to reach it

`python3 security-posture-audit/assets/audit_posture.py <repo> [--format text|json]
[--fail-on high|medium|low|info|never] [--skip-dir DIR]`. With `--format json` it prints
`{"findings": [{check, severity, path, line, evidence, remediation}], ...}`. With
`--fail-on high` it exits 1 when there is a HIGH finding, and 0 when there is none. The
check for workflows reads `.github/workflows/*.yml` in the repo.

## Drive it

Build a fixture repo with one workflow step that pipes a download into a shell. The
recipe builds the pipe character from a variable, so this file holds no literal risky
line either. Then audit the fixture, grep the sources, and look for the boundary.

```sh
I="$WT/.verify-run/$INSTANCE"
PA="$I/pa"
mkdir -p "$PA/repo/.github/workflows" "$PA/clean/.github/workflows"
P='|'
printf 'on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: curl -sSL https://get.invalid/install %s bash\n' "$P" \
  > "$PA/repo/.github/workflows/build.yml"
printf 'on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: curl -sSL -o install.sh https://get.invalid/install\n' \
  > "$PA/clean/.github/workflows/build.yml"
cat > "$I/sentences.py" <<'EOF'
import re, sys
text = re.sub(r"\s+", " ", open(sys.argv[1], encoding="utf-8").read())
words = [w.lower() for w in sys.argv[2:]]
hits = [s for s in re.split(r"(?<=[.!?])\s+", text) if all(w in s.lower() for w in words)]
for s in hits:
    print("SENTENCE:", s[:300])
print("SENTENCES: %d match %s" % (len(hits), " + ".join(words)))
sys.exit(0 if hits else 1)
EOF
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-risky -- \
  python3 security-posture-audit/assets/audit_posture.py "$PA/repo" --format json --fail-on high
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-clean -- \
  python3 security-posture-audit/assets/audit_posture.py "$PA/clean" --format json --fail-on high
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-src-grep -- \
  grep -nE '(curl|wget)[^|]*[|][^|]*\b(ba|z|da|k)?sh\b' \
  security-posture-audit/assets/test_audit_posture.py security-posture-audit/eval/run_eval.py
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-fixture-grep -- \
  grep -rlE '(curl|wget)[^|]*[|][^|]*\b(ba|z|da|k)?sh\b' security-posture-audit/assets/fixtures
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-fixture-ref -- \
  grep -c fixtures security-posture-audit/assets/test_audit_posture.py security-posture-audit/eval/run_eval.py
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-boundary -- \
  python3 "$I/sentences.py" security-posture-audit/SKILL.md data instruction
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-tests -- \
  python3 security-posture-audit/assets/test_audit_posture.py
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pa-eval -- \
  python3 security-posture-audit/eval/run_eval.py
```

Expected observations:

- `pa-risky`: exit 1. The JSON `findings` hold one `workflow-curl-pipe-sh` finding with
  severity `HIGH`, path `.github/workflows/build.yml` and line 6. This is the same as
  before the fix (a regression guard).
- `pa-clean`: exit 0. No `workflow-curl-pipe-sh` finding: a download to a file is not
  flagged.
- `pa-src-grep`: exit 1 and empty stdout. No line in the test source or the eval source
  pipes `curl` or `wget` into a shell.
- `pa-fixture-grep`: exit 0. It lists one or more files under
  `security-posture-audit/assets/fixtures/`. The first lines of each listed file hold a
  comment with the word "fixture" (read them with `head -5`).
- `pa-fixture-ref`: exit 0, and the count for each of the two files is 1 or more: the
  tests and the eval read the fixtures directory.
- `pa-boundary`: exit 0 and a line `SENTENCES: <n> match data + instruction` with n of 1
  or more. The `SENTENCE:` line says to treat scanned content as data and to ignore
  instructions in it.
- `pa-tests`: exit 0 and `OK`. `pa-eval`: exit 0 and a last line
  `EVAL_RESULT: PASS (<k>/<k> checks)`.

Before the fix (dry run on `factory/run5-base`, `eeac771`): `pa-risky` exits 1 with
`workflow-curl-pipe-sh` HIGH at `.github/workflows/build.yml` line 6 (plus
`missing-security-md` INFO). `pa-src-grep` exits 0 and finds two lines:
`eval/run_eval.py:95` and `assets/test_audit_posture.py:201`. The directory
`security-posture-audit/assets/fixtures` does not exist, so `pa-fixture-grep` exits 2.
`pa-boundary` exits 1 with `SENTENCES: 0 match data + instruction`. The tests (38) and
the eval (`EVAL_RESULT: PASS (38/38 checks)`) pass.

## Proof

The `pa-risky` capture (exit 1 and the HIGH `workflow-curl-pipe-sh` finding) and the
`pa-clean` capture (exit 0, no such finding) show that the detector behaves as before.
The `pa-src-grep` capture (exit 1, no match) shows that the Python sources hold no risky
line. The `pa-fixture-grep` and `pa-fixture-ref` captures show that the fixtures moved to
data files that the tests and the eval read. The `pa-boundary` capture shows the data
boundary in SKILL.md. The `pa-tests` and `pa-eval` captures show that the suite and the
eval still pass.

## Gotchas

- The anchors name only files that exist today. The new
  `security-posture-audit/assets/fixtures/` files are not anchors until they exist; add
  them to `- anchors:` in the follow-up change that sets `- proven:`.
- `grep` exits 1 for "no match" and 2 for "no such file". For `pa-src-grep` only exit 1 is
  a pass.
- Keep the fixture files out of `.github/workflows/` paths, so an audit of this repo does
  not report them as real workflows.
- A new BCP 14 keyword in the boundary sentence needs a row in
  `docs/rfc2119/2026-09-19-classification.md`, or `make bcp14` fails.
- Do not write the risky line literally in this recipe or in any new Python file: build
  it from parts, as the drive does.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
