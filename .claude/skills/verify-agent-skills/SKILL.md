---
name: verify-agent-skills
description: >-
  Prove a change to the agent-skills repo works by driving its skill CLIs the way a user
  does: forge.py (feature maps, lint), verify_evidence.py (the evidence recorder),
  detect_stack.py (verifier-installer), spec_lint.py and spec_to_tasks.py
  (spec-first-planning) and conductor.py (factory-conductor's evidence gate), each on
  fresh fixtures in its own instance directory. Use when you verify a factory-conductor
  task in this repo, or need SHA-bound evidence that one of these CLIs does what its
  feature map says.
---

# verify-agent-skills

agent-skills is a collection of Agent Skills. Its user-facing surface is a set of
Python command-line tools that a person or an agent runs from a terminal. This skill
drives those CLIs from the checkout under test, against fixtures that
`scripts/drive.py` builds in a per-instance directory, and records what they printed and
wrote. `drive.py` prints JSON for every command.

Session variables, used by every command below:

```sh
WT="$(git rev-parse --show-toplevel)"          # the checkout under test (a task worktree under factory-conductor)
SKILL="$WT/.claude/skills/verify-agent-skills"
INSTANCE="<a name of your own, such as v1>"    # one per verifier; never share one
VERIFIER="<your agent id>"                     # under factory-conductor: the id the verifier brief gives you; never the writer's id
```

Always drive the CLIs under `$WT`, never an installed copy under `~/.claude/skills`:
the installed copy is not the code under test.

## Launch

There is no server and no network port: each CLI is short-lived. Launching an instance
means giving it a directory of its own, `$WT/.verify-run/$INSTANCE/`, where every fixture
and capture for that instance lives. Two instances never share a directory, so two
verifiers can drive side by side. The instance is ready when the doctor below prints
`"ok": true`; it takes under 5 seconds, so stop waiting after 30.

```sh
mkdir -p "$WT/.verify-run/$INSTANCE"
python3 "$SKILL/scripts/drive.py" doctor --instance "$INSTANCE"
```

Fixtures (each prints JSON with the absolute `root` it built; `--name` must be new in the
instance):

```sh
python3 "$SKILL/scripts/drive.py" fixture verify-skill --instance "$INSTANCE" --name vs        # notes-app verify skill, git repo, one commit
python3 "$SKILL/scripts/drive.py" fixture commit --instance "$INSTANCE" --name vs --put REL=SRC # change a file in it and commit
python3 "$SKILL/scripts/drive.py" fixture stack --instance "$INSTANCE" --name rs --lang rust   # also python, typescript, go
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name gate --feature notes-create=a.txt
```

## Doctor

One read-only check: Python 3.10+, git on PATH, and every CLI in this checkout starts
without a traceback. It writes nothing. Exit 0 and `"ok": true` mean the instance is
worth driving.

```sh
python3 "$SKILL/scripts/drive.py" doctor --instance "$INSTANCE"
```

Record its result for this head (the conductor reads it):

```sh
python3 $SKILL/scripts/verify_evidence.py doctor --instance "$INSTANCE" --ok --check cli=pass --check git=pass --verifier "$VERIFIER"
```

## Drive

Run every CLI through `drive.py capture`, which runs the command from `$WT`, saves
argv, exit status, stdout and stderr to `$WT/.verify-run/$INSTANCE/captures/<name>.json`,
prints that JSON and exits with the command's own status. Address features by their
command names, flags and printed result lines (`LINT_RESULT:`, `EVIDENCE:`, `BACKEND:`),
never by position in the output.

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name lint-good -- \
  python3 verification-skill-forge/assets/forge.py lint "$WT/.verify-run/$INSTANCE/vs/.claude/skills/verify-notes"
```

The per-feature recipes are in `features/`; start at `features/README.md`.

## Evidence

- Exercise the real user path. Never internal setters, never test-only endpoints.
- Capture the action *and* the resulting state, not just a final screenshot.
- Verify side effects (rows inserted, files written, messages sent, webhooks fired) alongside what is visible.
- Mocks only where a production boundary already isolates the external system.
- Where the safe path is a dry-run or test mode, verify what it *actually* skips by observing files, network and git refs, not by trusting its name. Some dry-runs still touch the network or open a browser.
- Evidence goes to `.verify/<instance>/<feature-id>/<sha>/`, one directory per feature per head, recorded with `scripts/verify_evidence.py record`.
- Harness output is JSON wherever the harness can emit it, so a verifier can read it without a human.
- Cleanup removes instances, never evidence.

Set `FEATURE` to the feature id, `ACTION` to the commands you drove, `OBSERVED` to the
result lines and exit codes you saw, `EFFECT` to the files or records the commands wrote
(or `none: <why>`), and `CAPTURE` to the capture name, then:

```sh
python3 "$SKILL/scripts/verify_evidence.py" record --instance "$INSTANCE" --feature "$FEATURE" \
  --verifier "$VERIFIER" --result pass --action "$ACTION" --observed "$OBSERVED" \
  --side-effect "$EFFECT" --artifact "$WT/.verify-run/$INSTANCE/captures/$CAPTURE.json"
```

Under factory-conductor, export `VERIFY_EVIDENCE_DIR=<run root>/.verify` before you
record. The gated-run fixture has its own `.verify`: when you record into it, set the
variable on that one command only (`VERIFY_EVIDENCE_DIR=<fixture root>/.verify python3
...`), so your own records still land where the conductor reads them.

## Cleanup

Nothing keeps running after a drive, so cleanup only removes the instance directory with
its fixtures and captures. Copy any capture you still need into a record first: `record`
stores its own copy of every artifact.

```sh
python3 "$SKILL/scripts/drive.py" cleanup --instance "$INSTANCE"
```
