# tsn-discovery: test-safety-net runs a Node repo's own TypeScript only on opt-in

- id: tsn-discovery
- proven: no
- anchors: test-safety-net/assets/rank_risk.py:main, test-safety-net/assets/rank_risk.py:rank, test-safety-net/assets/stack_node.py:discover_units, test-safety-net/assets/stack_node.py:_units_precise, test-safety-net/assets/stack_node.py:_typescript_lib, test-safety-net/assets/test_stack_node.py, test-safety-net/references/parameters.md, test-safety-net/references/stacks.md, test-safety-net/SKILL.md

## What it is

**Status: planned (run 5).** After the fix, the Node "precise discovery" path of
test-safety-net runs only when the user passes the opt-in flag `--precise` to
`rank_risk.py`. That path loads `<repo>/node_modules/typescript/lib/typescript.js` (found
by `stack_node._typescript_lib`) in a `node` process (`stack_node._units_precise`), so it
runs the analysed repo's own code. The default run starts no `node` process, loads none of
the repo's files as code, and reports `"discovery": "heuristic"`. Today the precise path
is the default and `--no-precise` declines it. Socket flags this in
`assets/test_stack_node.py` and `references/parameters.md` (line 20).

After the fix `--no-precise` MAY stay as an accepted flag that changes nothing. The
recipe does not depend on it.

## How to reach it

`python3 test-safety-net/assets/rank_risk.py <repo> [--top-n N] [--since EXPR]
[--stack python|node|go|rust] [--precise]`. It prints JSON on stdout with the keys
`stack`, `discovery` (`"precise"` or `"heuristic"`) and `units_discovered`, and writes
`note:` lines on stderr (`note: stack=node ...`, and
`note: node precise discovery declined (<why>); using the heuristic reader` when it loaded
a toolchain that did not answer).

## Drive it

Build a Node fixture whose `typescript.js` only appends a line to a marker file in the
instance directory. It is not a real compiler, so a run that loads it then declines and
falls back to the heuristic. The marker shows whether the repo's code ran.

```sh
I="$WT/.verify-run/$INSTANCE"
TS="$I/tsn"
R="$TS/repo"
mkdir -p "$R/src" "$R/node_modules/typescript/lib"
printf '{"name":"tsn-fixture","version":"1.0.0","scripts":{"test":"node --test"}}\n' > "$R/package.json"
printf 'export function add(a: number, b: number): number {\n  return a + b;\n}\n\nexport function sub(a: number, b: number): number {\n  return a - b;\n}\n' > "$R/src/math.ts"
printf 'require("fs").appendFileSync(%s, "loaded\\n");\nmodule.exports = {};\n' "\"$TS/ts-ran.marker\"" \
  > "$R/node_modules/typescript/lib/typescript.js"
command -v node > "$TS/node-path.txt" || echo "no node" > "$TS/node-path.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tsn-default -- \
  python3 test-safety-net/assets/rank_risk.py "$R"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tsn-default-marker -- \
  test -e "$TS/ts-ran.marker"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tsn-optin -- \
  python3 test-safety-net/assets/rank_risk.py "$R" --precise
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tsn-optin-marker -- \
  test -e "$TS/ts-ran.marker"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name tsn-tests -- \
  python3 test-safety-net/assets/test_stack_node.py
```

Expected observations:

- `tsn-default`: exit 0. The JSON has `"stack": "node"`, `"discovery": "heuristic"` and
  `units_discovered` 2. Stderr has no `node precise discovery declined` line, because no
  toolchain was loaded.
- `tsn-default-marker`: exit 1. The marker does not exist: the default run ran no repo
  code.
- `tsn-optin`: exit 0, `"stack": "node"` and `units_discovered` 2. `discovery` is
  `"heuristic"` because the fake compiler gives no answer, and stderr has a
  `note: node precise discovery declined (...)` line.
- `tsn-optin-marker`: exit 0. The marker exists and holds `loaded`: the opt-in run
  loaded the repo's `typescript.js`.
- `tsn-tests`: exit 0 and `OK` (its `TestPreciseDiscovery` cases skip when no real
  `typescript` is found; a skip is not a failure).

If `node` is missing (`node-path.txt` says `no node`): `tsn-default` and
`tsn-default-marker` still prove the default path (no marker, `heuristic`). `tsn-optin`
then prints `note: node precise discovery declined (could not run node)` and
`tsn-optin-marker` exits 1. Record the opt-in half as not proven on that host, with the
reason, and prove it on a host with `node`.

Before the fix (dry run on `factory/run5-base`, `eeac771`, node v22.16.0):
`tsn-default` exits 0 with `"discovery": "heuristic"`, `units_discovered` 2 and the
stderr line `note: node precise discovery declined (1 file of 1 unreadable); using the
heuristic reader`. `tsn-default-marker` exits 0: the marker exists and holds `loaded`, so
the default run ran the repo's `typescript.js`. With `--no-precise` the marker is not
written and stderr has no `declined` line. `--precise` does not exist yet, so `tsn-optin`
exits 2 with an argparse `unrecognized arguments: --precise` error.

## Proof

The `tsn-default` capture (`heuristic`, no `declined` line) and the `tsn-default-marker`
capture (exit 1) show that the default run executes no repo code. The `tsn-optin` capture
(the `declined` line) and the `tsn-optin-marker` capture (exit 0) show that the opt-in
flag runs the repo's toolchain. The `tsn-tests` capture shows the unit suite passes.

## Gotchas

- Delete the marker before you run the default case again: `tsn-optin` writes it, and a
  marker left over from an earlier run gives a false fail.
- `units_discovered` is 2 on both paths here only because the fake compiler gives no
  answer. With a real `typescript`, the opt-in run reports `"precise"` and can find more
  units.
- The marker path is written into the fake `typescript.js` as an absolute path. Build
  the fixture in this instance, never copy it from another one.
- Go's precise path runs the skill's own `go/ast` helper, not repo code. Whether the fix
  also puts Go behind `--precise` is the fixer's choice; this recipe drives only Node.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
