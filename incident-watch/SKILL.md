---
name: incident-watch
description: >-
  The live-incident detector of the software factory. Use when the user asks for
  incident-watch, to "watch production for incidents" or to "probe prod". `incident probe`
  (incident.py, stdlib) loads the release recipe with release-conductor's own loader, runs
  only its read-only `health`, `version_probe` and `prod_smoke` commands (argv lists, no
  shell, a timeout each), records each result in a git-ignored state directory and prints
  one `PROBE:` line per command and a `PROBE_RESULT:` summary. Any other command is refused
  with exit 2 and nothing runs. Partly built: opening an incident record after 2 or more consecutive failed probes, the
  `incident` signal for ops-intake, the rollback command shown word for word and
  `incident close` are not built yet. It never runs a rollback or deploy: the owner
  mitigates. Not for a post-mortem (use bug-autopsy), not for a deploy or rollback (use
  release-conductor).
license: MIT
compatibility: Requires a POSIX system (macOS or Linux), Python 3.10+ (`python3`, `python`, `py -3` or `$SKILL_PYTHON`), stdlib only, offline, plus git. It reads the release recipe with the release-conductor skill, which has to be installed beside it.
metadata:
  author: dhanesh
  version: "0.1.0"
  tags: "software-factory,incident,triage,read-only-probe,partly-built"
---

# incident-watch

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

You watch production for incidents and triage them. The factory detects; the owner
mitigates (spec decision D1). A probe runs only the commands the release recipe lists as
read-only: `health`, `version_probe` and each `prod_smoke` entry (spec constraint S4).

**Status: partly built.** `incident probe` ships (R22): it runs the three read-only lists,
records each result and prints one line per command. It refuses, with exit 2 and before
anything runs, any requested command outside the three lists (R23). Not built yet: opening
one incident record and writing
an `incident` signal into ops-intake after 2 or more consecutive failed probes (R24), the
record's rollback command shown word for word (R25), and `incident close --by <name>`
(R26). The probe already keeps the consecutive-failure count, but today nothing opens a
record from it. The design is `docs/factory/2026-11-04-autonomous-factory-spec.md` (R22 to
R26, decision D1) in the agent-skills repository.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'incident-watch' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

**Python interpreter.** Most commands in this skill and its references are written with `python3`.
Find the interpreter once, before the first of them. If `$SKILL_PYTHON` is set, use it. Otherwise,
on macOS, if `command -v python3` prints `/usr/bin/python3` and `xcode-select -p` fails, that
`python3` is the install stub: skip it without running it. Use the first of `python3`, `python` and
`py -3` that is not skipped and for which
`<interpreter> -c 'import sys; sys.exit(sys.version_info < (3, 10))'` exits 0. Write the interpreter
you found in place of `python3` in every command, and in any `Bash(...)` permission rule you
suggest. Commands that start with `uv run` or `{python}` keep their own form. In a session resumed
under a `Bash(...)` allowlist, skip this lookup and use the interpreter that the allowlist names. If
none passes, follow this skill's own fallback for running without Python where it gives one;
otherwise stop and tell the user that this skill needs Python 3.10 or newer.

The command is `python3 "$SKILL_DIR/assets/incident.py" --root <repo> probe`. It reads
`<repo>/.release/recipe.json` with release-conductor's `load_recipe`, so the recipe format
and its checks are release-conductor's (its release protocol reference describes them).
`{env}` in a probe command is release-conductor's production value. Pass
`--version <v>` and `--commit <sha>` when a probe command carries `{version}` or
`{commit}`; without them the probe refuses and runs nothing. `--timeout <seconds>` sets the
limit per command (default 60). `--only <list>` (repeatable) runs only that list; the name
is exactly `health`, `version_probe` or `prod_smoke`. Any other name (`rollback`,
`deploy_prod`, a path, a command), any extra argument after `probe`, and any probe entry
whose program word holds a `{token}` exit 2 with `PROBE: refused: <reason>`, and nothing
runs, not even the allowed lists (R23). `--skills-dir <dir>` (before `probe`, repeatable) makes the
tool look for release-conductor only in that directory; it is for tests.

## When to use

The user asks for incident-watch by name, asks to "watch production for incidents", to
"probe prod" or to "check whether production is healthy".

Boundaries: a deploy or a rollback is `release-conductor`, and only after the owner's yes;
a post-mortem of an incident that is over is `bug-autopsy`; triage of signals into planning
is `ops-intake`.

## Workflow

1. **Run one probe.** Run `python3 "$SKILL_DIR/assets/incident.py" --root <repo> probe`.
   Exit 2 with `PROBE: refused: <reason>` means nothing ran: report the reason (no
   release-conductor, an invalid or missing recipe, a missing `--version` or `--commit`, a
   state directory that is not git-ignored, a name or argument outside the three probe
   lists).
2. **Read each PROBE: line.** There is one per command, in order: `health`,
   `version_probe`, then `prod_smoke[0]` and on. The word is `pass`, `fail` or `timeout`,
   with the exit code. The last line is
   `PROBE_RESULT: <pass|fail> commands=<n> failed=<n> consecutive_failures=<n>`. Exit 0 is
   a pass; exit 3 is a failed probe (or another probe holds the lock).
3. **Report a failure without acting on it.** For a failed probe, name each failed command
   and the consecutive-failure count. The output tails are in
   `<repo>/.skill-contract/incidents/probes.jsonl`; quote from them only what the owner
   needs. Tell the owner that mitigation is theirs, through release-conductor.
4. **Verify the report.** Check it against the success criteria below, then re-check.

You MUST NOT run a rollback, a deploy or any other recipe command yourself, because
rollback and production deploy are never-grantable human gates (spec decision D1 and
constraint B1), and a wrong command against production does real harm.

You MUST NOT say that an incident record was opened, that a signal was written or that
`incident close` exists, because those parts are not built yet and a false claim hides an
incident the owner has to handle.

## Deliverable

A probe report: each `PROBE:` line as a table row (command, result, exit code), the
`PROBE_RESULT:` line, the consecutive-failure count and, for a failure, the owner's next
step (mitigate through release-conductor).

## Success criteria

- The report lists every command the probe ran, with its result.
- A failure is reported as a failure; no record, signal or rollback is claimed.
- No command outside `health`, `version_probe` and `prod_smoke` was run.

The outcome eval (`eval/run_eval.py`) runs the probe on a fixture recipe from
`assets/incident_testkit.py`, whose commands are local stubs, and grades its lines and
records. The unit suite is `assets/test_incident.py`.
