# forge-feature-map: lint a verify skill and read its feature map

- id: forge-feature-map
- proven: 9ebad708af9a37fdb0b81d0d0334f7de37dcc8f3
- anchors: verification-skill-forge/assets/forge.py, verification-skill-forge/references/feature-map.md

## What it is

A user runs `forge.py lint <verify dir>` to check that a generated verify skill and its
feature map are complete: every section filled, every feature file indexed, every anchor
alive. It prints one line per defect and ends with `LINT_RESULT: PASS` or
`LINT_RESULT: FAIL`.

## How to reach it

`python3 verification-skill-forge/assets/forge.py lint <verify dir>` from the repo root.
Other forge subcommands (`detect`, `scaffold`, `vendor`, `coverage`, `seed`, `finding`,
`check-maintain`) share the file; drive the one a change touches.

## Drive it

```sh
python3 "$SKILL/scripts/drive.py" fixture verify-skill --instance "$INSTANCE" --name fm
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name fm-lint-pass -- \
  python3 verification-skill-forge/assets/forge.py lint "$WT/.verify-run/$INSTANCE/fm/.claude/skills/verify-notes"
```

Expect exit 0 and stdout ending `LINT_RESULT: PASS`.

Then break the map and drive it again: write a copy of `features/README.md` that drops the
`notes-list` line into a scratch file, apply it with
`drive.py fixture commit --instance "$INSTANCE" --name fm --put .claude/skills/verify-notes/features/README.md=<scratch file>`,
and rerun the lint as `fm-lint-fail`.

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name fm-lint-fail -- \
  python3 verification-skill-forge/assets/forge.py lint "$WT/.verify-run/$INSTANCE/fm/.claude/skills/verify-notes"
```

Expect exit 1, a line naming `notes-list.md` as not indexed, and `LINT_RESULT: FAIL`.

## Proof

Both captures: the passing lint on the untouched fixture and the failing lint naming the
defect after one commit. The side effect is the fixture commit (`git -C <fixture> log`
shows two commits); lint itself writes nothing (`git -C <fixture> status --short` is empty
after it).

## Gotchas

- Drive `$WT/verification-skill-forge/assets/forge.py`, never the installed copy under
  `~/.claude/skills`, which is not the code under test.
- The fixture is a copy of the notes-app eval fixture; its vendored `verify_evidence.py`
  must equal the forge's, or lint fails on drift before it reaches your defect.
