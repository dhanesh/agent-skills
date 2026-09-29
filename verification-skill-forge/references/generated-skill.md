# The generated verify skill: section contract

A generated skill lives at `<app root>/.claude/skills/verify-<app>/`:

```text
verify-<app>/
  SKILL.md                 # frontmatter + Launch, Doctor, Drive, Evidence, Cleanup
  scripts/verify_evidence.py   # byte-identical copy of the forge's recorder (forge vendor)
  scripts/<harness>        # optional: the app-specific driver, JSON out
  features/README.md       # the feature map index
  features/<id>.md         # one per user-facing feature
```

Its reader is an agent that has never seen the app, mid-task. Every command is one it can
paste. `forge lint` enforces the mechanical half of this contract; the rest is review.

## Frontmatter

`name: verify-<app>` (the directory name) and a `description` naming the app, the surface
it drives and when to reach for it. Without the frontmatter the skill never registers,
so the cold-run agent never finds it.

## Launch

- Every command takes the instance name (`$INSTANCE` or `--instance`). Lint fails a
  Launch with no instance parameter.
- Per-instance port: `python3 "$SKILL/scripts/verify_evidence.py" port --instance
  "$INSTANCE"` claims one with an O_EXCL lock under `.verify-run/ports/`. Two owners can
  never hold the same port; `claim --port N` on a port another instance holds exits 3.
- Per-instance data store (a directory, a database name, a schema) and per-instance
  auth session (a cookie jar, a profile directory, a token file).
- Readiness: the signal (a log line, a health route answering for *this* instance) and
  the timeout. Lint fails a Launch that names neither.
- For a short-lived CLI there is no server: launch means build once, then every drive runs
  in its own PTY or temp directory keyed by the instance.

## Doctor

One read-only command answering "is this instance worth driving?": process up, the right
version or build, the port owned by this instance, auth valid. Record it with
`verify_evidence.py doctor --instance I --ok|--fail --check name=pass|fail ...`, which
writes `.verify/<instance>/doctor/<sha>/doctor.json`. Lint fails a Doctor command that
writes (`rm`, `kill`, a redirect to a file, a POST, SQL writes, git writes).

## Drive

Real selectors and commands from this repo. Stable handles only: ARIA roles and labels,
`data-*` attributes, route paths, CLI prompt strings, API routes. Lint fails screen
coordinates, `mouse.click(x, y)`, pressing Tab to move focus, and any mention of tab
order. The per-feature recipes live in `features/`.

## Evidence

Copy this block verbatim into the Evidence section (lint compares it line by line):

- Exercise the real user path. Never internal setters, never test-only endpoints.
- Capture the action *and* the resulting state, not just a final screenshot.
- Verify side effects (rows inserted, files written, messages sent, webhooks fired) alongside what is visible.
- Mocks only where a production boundary already isolates the external system.
- Where the safe path is a dry-run or test mode, verify what it *actually* skips by observing files, network and git refs, not by trusting its name. Some dry-runs still touch the network or open a browser.
- Evidence goes to `.verify/<instance>/<feature-id>/<sha>/`, one directory per feature per head, recorded with `scripts/verify_evidence.py record`.
- Harness output is JSON wherever the harness can emit it, so a verifier can read it without a human.
- Cleanup removes instances, never evidence.

Then show the record command:
`python3 "$SKILL/scripts/verify_evidence.py" record --instance "$INSTANCE" --feature <id>
--verifier "$VERIFIER" --result pass|fail --action "..." --observed "..." --side-effect
"..." --artifact <captured file>`. The recorder reads the head commit from git, copies
each artifact into `.verify/<instance>/<feature-id>/<sha>/` and pins it by sha256.
`--side-effect` is required (write `none: <why>` when there is truly none) and so is at
least one `--artifact`.

Under factory-conductor the verifier exports `VERIFY_EVIDENCE_DIR=<run root>/.verify`
so records from a task worktree land where the conductor reads them.

## Cleanup

Stop what this instance started (its pid file, its port), release its port claim and
delete `.verify-run/<instance>/`. Never kill by process name (`pkill`, `killall`), which
takes down another owner's instance too. Never delete `.verify/`: lint fails any
`rm`/`find` that touches it.

## Git hygiene

Add `.verify/` and `.verify-run/` to the target repo's `.gitignore` (or
`.git/info/exclude`) so evidence and run state are never committed by accident.
