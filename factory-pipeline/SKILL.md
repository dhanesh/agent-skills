---
name: factory-pipeline
description: >-
  The scheduled driver of the software factory. Use when the user asks for factory-pipeline,
  to "tick the pipeline" or to "run the whole factory on a schedule". One `pipeline tick`
  (pipeline.py, stdlib) walks the stages in order: sweep, growth review, idea follow-up,
  autopick, plan, derive grant, build, release stage and digest, and prints one
  `STAGE: <name> <result>` line per stage. It calls each installed sibling skill's own tool
  (ops-intake, growth-review, spec-first-planning) with no shell, and reports a missing one
  as unavailable. With no live pipeline grant it prints `STOP: no-grant` and exits 2; a
  held repo lock gives `STOP: locked`, exit 3; a pipeline-grant cap gives `STOP: cap
  <name>` and no new plan or run. Partly built: idea follow-up, plan, build, release stage
  and digest report `pending-R<n>`, and the schedule is not built yet.
  Every human gate stays with the owner. Not for running one approved plan to a PR (use
  factory-conductor), not for writing a plan (use spec-first-planning).
license: MIT
compatibility: Requires a POSIX system (macOS or Linux), Python 3.10+ (`python3`, `python`, `py -3` or `$SKILL_PYTHON`), stdlib only, offline. The stages call the sibling skills ops-intake, growth-review and spec-first-planning when they are installed.
metadata:
  author: dhanesh
  version: "0.1.0"
  tags: "software-factory,scheduler,pipeline,autonomy-grant,partly-built"
---

# factory-pipeline

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

You drive the software factory one tick at a time. A tick runs each factory stage in a
fixed order and calls the tool of the skill that owns that stage. The tick does the
mechanical part of each stage. It stops at the human gates: merge, production deploy,
rollback, spend, external messages, the release grant and idea decisions stay with the
owner.

**Status: partly built.** `pipeline tick` ships. First it takes the repo's pipeline lock
(R14, shipped), so only one tick runs at a time: a tick that finds the lock held prints
`STOP: locked: ...`, changes nothing and exits 3. Before any stage, it checks the standing
pipeline grant (R13, shipped): with no live grant it runs no stage, prints
`STOP: no-grant reason=<reason>` and exits 2. It runs the sweep, growth review and autopick
stages for real, and runs derive grant for each plan that the plan stage hands on. The plan
stage is not built yet, so it hands on no plan today. Before each stage that would start
a plan or run, it checks the grant's caps (R15, shipped): at a cap it prints
`STOP: cap <name>` and skips the stages that the cap gates. The idea follow-up, plan, build, release stage
and digest stages report `pending-R<n>`. `pipeline schedule install` is not built yet. The
design is `docs/factory/2026-11-04-autonomous-factory-spec.md` (R12 to R21) in the
agent-skills repository.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'factory-pipeline' 2>/dev/null | head -1)
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

The command is `python3 "$SKILL_DIR/assets/pipeline.py" --root <repo> tick`. The tick first
takes a non-blocking flock on `<repo>/.skill-contract/pipeline/.lock` and holds it until it
exits; the kernel drops it when the tick dies, so a killed tick never blocks the next one.
It proves that directory is git-ignored before it writes the lock file, and refuses with
`STOP: state-dir reason=<reason>` and exit 2 when it cannot. Before any
stage, the tick runs spec-first-planning's `contract_check.py check-pipeline-grant`. It goes
on only when that answers VALID for the newest pipeline grant that no revision supersedes,
and it prints `TICK: grant <id> expires_at <time>`. Derive grant then uses that grant id and
no other. Options: `tick --grant <pipeline grant id or envelope path>` checks that grant
instead of the newest, and `tick --growth-source <name>` names the intake source of growth
signals (default `growth`). Before autopick, plan and derive grant, and before each plan
after the first in derive grant, the tick checks the three caps against today's UTC
counts; before build, when it has grants to build, it checks only the dispatch cap,
because a grant already derived was counted when it was derived. The counts are: plans per day and open runs from spec-first-planning
`write_grant.py --pipeline-caps <grant> --root <repo>` (the counter `--under-pipeline`
refuses with), and dispatches per day from the `dispatch` events in factory-conductor's
`.skill-contract/runs/<run>/autonomy-log.jsonl` of each run under a derived grant. At a cap
it prints `STOP: cap <name> count=<n> limit=<m> stage=<stage>`; when a source cannot be
read it prints `STOP: cap uncounted source=<what> reason=<why>`. It appends the reason to
`.skill-contract/pipeline/cap-stops.jsonl`, prints `STAGE: <name> skipped cap=<name>` for
each later stage that the same cap gates (a dispatches stop also skips build), still runs the stages that only read, sweep or report, and exits 0.
`--skills-dir <dir>` (before `tick`, repeatable) makes the
tick look for sibling skills only in that directory; it is for tests.

## When to use

The user asks for factory-pipeline by name, asks to "tick the pipeline", or asks to "run
the whole factory on a schedule" or to "drive every stage unattended".

Boundaries: running one approved plan to an open PR is `factory-conductor`; writing a
spec and task plan is `spec-first-planning`; collecting and picking work is `ops-intake`;
carrying a merged change to production is `release-conductor`.

## The stages

The stage list, what each stage calls today and the result words are in
[references/overview.md](references/overview.md). In short: sweep runs `intake sweep`;
growth review runs `growth auto --source <name>` and hands its stdout to `intake import
--format intake-signals-jsonl --source <name>` as two separate processes; autopick runs
`intake autopick`; derive grant runs `write_grant.py --under-pipeline <grant> --root <repo>
--plan <plan>` for each plan the plan stage hands on. The tick finds each sibling skill
by its directory name in the roots that skill-contract `discover` searches. A missing
skill gives `STAGE: <name> unavailable`, and the tick goes on.

## Workflow

1. **Run one tick.** Run `python3 "$SKILL_DIR/assets/pipeline.py" --root <repo> tick`. Lines
   that start with two spaces and `<stage>|` are the stage tool's own output. When the tick
   prints `STOP: no-grant reason=<reason>` and exits 2, no stage ran: report the reason
   (`none`, `revoked`, `expired`, `stale`, `tracked` and so on), and tell the owner that a
   new pipeline grant is theirs to accept (`write_grant.py --pipeline` in
   spec-first-planning). When it prints `STOP: locked: ...` and exits 3, another tick is
   still running in this repo: report that, and do not delete the lock file or start the
   tick again in a loop. The running tick releases the lock when it exits. When it prints
   `STOP: cap <name> ...`, report the cap, its count and limit and the stage it stopped
   before; for `STOP: cap uncounted`, name the file the tick could not read. The owner
   decides whether to wait for the next UTC day, close a run or accept a new grant.
2. **Read every STAGE: line.** There are nine, in order. A result other than `ok` or `idle`
   needs a sentence in your report: `attention` (the tool exited 3), `refused` (exit 2, for
   example no intake config or no registered growth plan), `failed`, `timeout`,
   `unavailable` (the skill is not installed), `skipped` (a cap stopped the tick before
   that stage) or `pending-R<n>` (not built yet).
3. **Follow the tool lines.** A `NEXT:` or `STOP:` line in a stage's output belongs to that
   stage's skill. Read it in that skill's terms, and give the owner the steps that are theirs.
4. **Verify the report.** Check it against the success criteria below, then re-check.

You MUST NOT report a stage as done when its line says `pending-R<n>`, `unavailable`,
`refused`, `failed` or `timeout`, because the owner reads your report to learn which
stages ran and a false `done` hides work that waits.

You MUST NOT start a plan, derive a grant or run `conductor init` by hand after a
`STOP: cap` line, and you MUST NOT delete or edit an envelope, a run log or
`cap-stops.jsonl` to bring a count under its cap, because the caps are the owner's bound on
unattended work and the tick stops there by design (spec constraint O2).

You MUST NOT merge a pull request, accept a release grant, or write or register the pick
policy, the intake config or a pipeline grant for the tick, because each one is a human
gate that the owner keeps (spec constraint B1).

## Deliverable

A tick report: the nine `STAGE:` lines as a table (stage, result, detail), a sentence for
each result that is not `ok` or `idle`, and the owner's next step for each `STOP:` or
`NEXT:` line a stage printed, including a `STOP: cap` line.

## Success criteria

- The report shows nine stages in the order of the tick.
- Each stage that is not `ok` or `idle` has a plain reason.
- No stage that says `pending-R<n>` is called done or run.
- A `STOP: cap` line is reported with its cap and the stage it stopped before, and no
  `skipped` stage is called done.

The outcome eval (`eval/run_eval.py`) runs the tick on stub skills from
`assets/pipeline_testkit.py` and grades the nine lines; the unit suites are
`assets/test_pipeline_tick.py`, `assets/test_pipeline_lock.py` and
`assets/test_pipeline_caps.py`.
