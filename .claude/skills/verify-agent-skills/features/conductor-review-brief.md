# conductor-review-brief: brief a fresh reviewer and record its verdict

- id: conductor-review-brief
- proven: no
- anchors: factory-conductor/SKILL.md:The reviewer brief, factory-conductor/assets/conductor.py:cmd_review

## What it is

After a task's verify commands pass, factory-conductor dispatches a fresh reviewer. The
conducting session fills in the text template in the "The reviewer brief" section of
`factory-conductor/SKILL.md`: the task, the requirement, each acceptance criterion with
its pinned command, the constraints and the diff from a hardened `git diff` command. The
reviewer answers pass or fail. `conductor review` records that verdict. It refuses a
reviewer who is the task's writer, and under the evidence gate it refuses a review with
no reviewer id. A fail sends the task back for repair.

## How to reach it

- The brief: the `text` block under "## The reviewer brief" in `factory-conductor/SKILL.md`.
  The conducting session fills it in, or prints it filled from a plan with
  `python3 factory-conductor/assets/conductor.py brief reviewer <task> --plan <envelope> --base <ref> [--head <ref>] --root <repo>`.
  That command needs no run and writes no file. It exits 2 for an unknown task.
- The verdict: `python3 factory-conductor/assets/conductor.py review <task> --verdict pass|fail [--detail <text>] [--reviewer <id>] --root <run root>`.

## Drive it

The `gated-run` fixture stops after a pass review by `reviewer`, with T1 in state
`reviewing`, so `review` can be called on it again:

```sh
python3 "$SKILL/scripts/drive.py" fixture gated-run --instance "$INSTANCE" --name rb --feature notes-create=a.txt
```

Expect exit 0 and JSON with `root`, `worktree` and `sha`. Its `steps` end with
`REVIEW: T1 pass`. The run branch is `factory/gate` (the `run_branch` key of
`<root>/.skill-contract/runs/<run-id>/state.json`). Make the diff that the brief pastes,
with the exact command from the brief, then drive the three verdict paths:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rb-diff -- \
  git --no-replace-objects -C <worktree> -c core.fsmonitor=false -c core.hooksPath=/dev/null \
  diff --no-ext-diff --no-textconv --text --no-color factory/gate...<sha>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rb-noid -- \
  python3 factory-conductor/assets/conductor.py review T1 --verdict pass --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rb-writer -- \
  python3 factory-conductor/assets/conductor.py review T1 --verdict pass --reviewer writer --root <root>
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name rb-fail -- \
  python3 factory-conductor/assets/conductor.py review T1 --verdict fail \
  --detail "adds an unasked change" --reviewer reviewer2 --root <root>
```

- `rb-diff`: exit 0. The diff adds the line `+work` to `a.txt`.
- `rb-noid`: exit 2. stderr says
  `the evidence gate records who reviews: review T1 --reviewer <the reviewer's agent id>`.
- `rb-writer`: exit 2. stderr says
  `task T1: writer wrote this task and cannot review it; dispatch a fresh reviewer`.
- `rb-fail`: exit 3 and `REVIEW: T1 fail`. Then
  `python3 factory-conductor/assets/conductor.py status --root <root>` prints
  `STATUS: T1 verifying review=fail owner="writer"` as its first line.

## Proof

The captures' lines and exit codes, and the run's
`<root>/.skill-contract/runs/<run-id>/autonomy-log.jsonl`. It gains a `review_refused`
event with `"reason": "writer"`, then a `review` event with `"verdict": "fail"`,
`"reviewer": "reviewer2"` and the verified head, then a `dispatch` event with
`"kind": "repair"`. The refusal with no reviewer id adds no event.

## Gotchas

- `<root>`, `<worktree>` and `<sha>` come from the fixture's JSON; copy them, do not guess.
- `writer` and `reviewer` are the fixture's identities, not yours.
- After `rb-fail`, T1 is back in `verifying`, so a second `review` call is refused until
  the task is verified again. Build a new fixture (another `--name`) to drive again.
- The brief's diff command neutralises git config, attribute and replace-ref tricks. Do
  not drop its flags when you reproduce it.
