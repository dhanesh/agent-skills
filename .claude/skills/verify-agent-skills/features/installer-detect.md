# installer-detect: detect a repo's stacks and plan its missing verifier rails

- id: installer-detect
- proven: no
- anchors: verifier-installer/assets/detect_stack.py:detect

## What it is

verifier-installer runs `detect_stack.py` on a repository. The tool walks the tree
offline, finds the stacks (`make`, `node`, `python`, `go`, `rust`), records which of the
four rails (`build`, `ci`, `format`, `test`) already exist, and proposes a command and a
file for each missing rail. It prints one JSON plan. The lists are sorted and the keys
are sorted, so the same tree always gives the same bytes.

## How to reach it

`python3 verifier-installer/assets/detect_stack.py <repo-dir>`. It has no flags.

## Drive it

```sh
python3 "$SKILL/scripts/drive.py" fixture stack --instance "$INSTANCE" --name py --lang python
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name id-py -- \
  python3 verifier-installer/assets/detect_stack.py "$WT/.verify-run/$INSTANCE/py"
python3 "$SKILL/scripts/drive.py" fixture stack --instance "$INSTANCE" --name rs --lang rust
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name id-rs -- \
  python3 verifier-installer/assets/detect_stack.py "$WT/.verify-run/$INSTANCE/rs"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name id-missing -- \
  python3 verifier-installer/assets/detect_stack.py "$WT/.verify-run/$INSTANCE/nope"
```

- Python fixture: exit 0. The JSON has `"stacks": ["python"]`, `"errors": []` and
  `"missing": ["build", "ci", "format", "test"]`. `proposals` has one item per missing
  rail: `build` is `python3 -m compileall -q -f .`, `test` is
  `python3 -m unittest discover -s tests`, and `format` is `make format` with
  `"placeholder": true`, because the fixture adopts no formatter.
- Rust fixture: exit 0, `"stacks": ["rust"]` and `"missing": ["ci"]`.
- A path that is not a directory: exit 2 and `error: not a directory: <path>` on stderr.
  No argument: exit 2 and `usage: detect_stack.py <repo-dir>`.

Run the Python capture twice and compare the two `stdout` values: they are identical.

## Proof

The captures' JSON plans and exit codes. The tool writes nothing: the fixture tree is the
same before and after (`git -C "$WT/.verify-run/$INSTANCE/py" status --short` prints
nothing, because the fixture is a committed git repo).

## Gotchas

- The tool skips `.git`, `node_modules`, `target`, `build`, `dist` and other vendored or
  cache directories, so a stack that lives only there is not detected.
- Exit 0 does not mean the repo is clean: recoverable problems go to the plan's `errors`
  list.
