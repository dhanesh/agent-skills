# grant-check: write an autonomy grant and ask whether it covers an action

- id: grant-check
- proven: no
- anchors: docs/skill-contract/SPEC.md:autonomy-grant/v1, docs/skill-contract/reference/contract_check.py:check_grant, spec-first-planning/assets/write_grant.py:build_grant

## What it is

An autonomy grant is a user's written yes to unattended work. spec-first-planning's
`write_grant.py` writes it after the user accepts: an `autonomy-grant/v1` envelope that
pins a spec and its task plan, with a gate per action class (`auto`, `grant` or `ask`).
Every skill that acts unattended first asks the reference checker
`contract_check.py check-grant` whether a live grant covers the action. The rules are in
the "autonomy grant (commandment 10)" part of `docs/skill-contract/SPEC.md`.

## How to reach it

- `python3 spec-first-planning/assets/write_grant.py --root <repo> --spec <rel spec> --plan <plan envelope> --answers <answers.json> --accepted-by <name>`
- `python3 docs/skill-contract/reference/contract_check.py check-grant [FILE] --root <repo> --action <class> [--subject <path>] [--worktree <path>] [--json]`
- `python3 docs/skill-contract/reference/contract_check.py revoke-grant [--id <id>] --root <repo>`

`check-grant` exits 0 for `COVERED`, 3 for `ASK` or `NONE`, 2 for `INVALID` and 1 for a
usage error. Its last line is `GRANT: ...`. Each adopting skill has a byte-identical copy
of the checker in its `assets/`.

## Drive it

Build a git repo with a decision-closed spec and its task plan. The branch must match
the grant's `branch_pattern`, so name it `factory/run`. The answers file needs an
`expires_at` in the future, so compute it:

```sh
G="$WT/.verify-run/$INSTANCE/gc"
mkdir -p "$G/docs" && git init -q -b factory/run "$G"
cp docs/factory/2026-10-22-proof-tiered-verification-spec.md "$G/docs/spec.md"
python3 spec-first-planning/assets/spec_to_tasks.py --envelope "$G" "$G/docs/spec.md"
python3 - "$WT/.verify-run/$INSTANCE/gc-answers.json" <<'PY'
import datetime, json, sys
exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"branch_pattern": "factory/*", "expires_at": exp,
           "gate_policy": {"read_only": "auto", "local_reversible": "grant"}},
          open(sys.argv[1], "w"))
PY
PLAN=$(ls "$G"/.skill-contract/envelopes/task-plan-v1-*.json)
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gc-write -- \
  python3 spec-first-planning/assets/write_grant.py --root "$G" --spec docs/spec.md \
  --plan "$PLAN" --answers "$WT/.verify-run/$INSTANCE/gc-answers.json" --accepted-by Verifier
```

`spec_to_tasks.py` ends with `ENVELOPE: <path>`. `write_grant.py` exits 0. It prints a
`NOTE:` that the grant is kept out of commits, then `GRANT: <G>/.skill-contract/envelopes/autonomy-grant-v1-<time>-<hex>.json`.
Then ask the checker:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gc-local -- \
  python3 docs/skill-contract/reference/contract_check.py check-grant --root "$G" --action local_reversible
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gc-merge -- \
  python3 docs/skill-contract/reference/contract_check.py check-grant --root "$G" --action merge
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gc-subject -- \
  python3 docs/skill-contract/reference/contract_check.py check-grant --root "$G" --action local_reversible --subject docs/other.md
```

- `local_reversible`: exit 0, `GRANT: COVERED id=<grant id> class=local_reversible gate=grant`.
  `read_only` gives `gate=auto`.
- `merge`: exit 3, `GRANT: ASK id=<grant id> reason=gate-ask`. `push_branch` and `spend`
  give the same answer under this grant.
- A subject that the grant does not pin: exit 3, `GRANT: ASK id=<grant id> reason=subject`.
  `--subject docs/spec.md` is covered.
- `--json` prints one object with `status`, `gate`, `id`, `path`, `reason` and
  `violations`.

Refusals and revocation:

- `write_grant.py` with `"merge": "auto"` added to `gate_policy` exits 1 with
  `REFUSED: gate_policy['merge'] must be ask: a grant never covers it (A8)` and writes no
  envelope.
- `check-grant` on a root with no grant exits 3 with `GRANT: NONE`.
- `check-grant --action bogus` exits 1 with a usage line that lists the classes.
- `revoke-grant --root "$G"` exits 0 with `REVOKED: <grant id>`. Then `check-grant`
  exits 3 with `GRANT: ASK id=<id of the revocation> reason=revoked`.

## Proof

The captures' `GRANT:` lines and exit codes. The side effects: one
`autonomy-grant-v1-*.json` under `$G/.skill-contract/envelopes/`, and the line
`.skill-contract/envelopes/autonomy-grant-v1-*` in `$G/.git/info/exclude`. The refused
write adds no envelope (`ls "$G/.skill-contract/envelopes"` before and after).
`check-grant` writes nothing; `revoke-grant` writes one new revision envelope.

## Gotchas

- On a default branch (such as `main`), every action gives `ASK ... reason=default-branch`.
  Run from a branch that matches `branch_pattern`.
- A grant that git tracks covers nothing. Do not commit the fixture's grant.
- `--spec` is relative to `--root`. `spec_to_tasks.py --envelope` needs the spec path that
  it can open from `$WT`, so give it the absolute path.
- A grant lives at most 7 days, and `expires_at` must be in the future when you write it.
