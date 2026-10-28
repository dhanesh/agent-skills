# ab-validate: compare the branch against its baseline

- id: ab-validate
- proven: no
- anchors: scripts/ab-validate.py

## What it is

`make ab-validate` checks the baseline (the merge base with `origin/main` or `main`) out
into a temporary worktree, runs the same fixtures through both trees, and labels each row
IMPROVED, HELD, HELD*, WORSE or UNPROVEN. WORSE and UNPROVEN fail it.

## How to reach it

`make ab-validate [BASE=<ref>]`, or `python3 scripts/ab-validate.py`, from the repo root.

## Drive it

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ab -- make ab-validate
```

Expect exit 0 and a table with no WORSE or UNPROVEN row. A clone with no `main` prints
SKIP and also exits 0: drive it in the task worktree, which shares the repository's refs.

## Proof

The capture's rows: each claimed row IMPROVED, each guard HELD or HELD*. The side effect is a
temporary baseline worktree that the script removes; `git worktree list` afterwards shows
no leftover entry.

## Gotchas

- A SKIP proves nothing; check that the capture names the baseline commit.
- A row whose `since` commit is outside the baseline is a live claim and must move the
  numbers, or it reports UNPROVEN.
