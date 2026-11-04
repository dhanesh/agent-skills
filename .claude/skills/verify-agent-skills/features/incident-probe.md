# incident-probe: detect live incidents with the release recipe's read-only probes

- id: incident-probe
- proven: no
- anchors: incident-watch/assets/incident.py:cmd_probe, incident-watch/assets/incident.py:probe_commands, incident-watch/assets/incident.py:run_probe, incident-watch/assets/incident.py:ensure_dir, incident-watch/assets/incident.py:after_probe, incident-watch/assets/incident.py:check_only, incident-watch/assets/incident.py:open_record, incident-watch/assets/incident.py:signal_line

## What it is

**Status: partly built.** `incident probe` ships (R22). It loads `.release/recipe.json`
with release-conductor's own `load_recipe` (imported from the release-conductor skill
beside incident-watch). It runs only the recipe's `health`, `version_probe` and each
`prod_smoke` command, as argv lists with no shell and a timeout each. It appends each
result (time, name, argv, exit code, a 2000-character output tail) to
`.skill-contract/incidents/probes.jsonl`, which a `*` `.gitignore` in that directory keeps
out of git. It keeps the consecutive-failure count in `state.json`. It refuses any other
command with exit 2 before anything runs (R23). After 2 or more consecutive failed probes
it opens one incident record, `.skill-contract/incidents/records/<id>.json`, and appends one
`intake-signals-jsonl` line of kind `incident` to `.skill-contract/incidents/signals.jsonl`
(R24). The design is
`docs/factory/2026-11-04-autonomous-factory-spec.md` (R22 to R26, D1, S4).

## How to reach it

`python3 incident-watch/assets/incident.py --root <repo> [--skills-dir <dir>] probe
[--version V] [--commit C] [--timeout S] [--only health|version_probe|prod_smoke]`. `--skills-dir` makes the tool look for
release-conductor only in that directory. `python3 incident-watch/assets/incident_testkit.py
fixture <dir> [--fail NAME]` makes `<dir>` a git repo with a valid recipe whose every
command runs a local stub (`<dir>/stubs/stub.py <name>`). Each stub call is logged to
`<dir>/stubs/calls.log`, and a stub named in `--fail` exits 1.

Shipped:

- `incident probe` with passing stubs: four `PROBE:` lines (`health`, `version_probe`,
  `prod_smoke[0]`, `prod_smoke[1]`), each `pass rc=0`, then
  `PROBE_RESULT: pass commands=4 failed=0 consecutive_failures=0`, exit 0. One recorded
  pass, and no incident record.
- A failing probe command: `PROBE: <name> fail rc=<n>`, `PROBE_RESULT: fail ...`, exit 3,
  and the count goes up by one. A pass sets it back to 0.
- A refusal (no release-conductor, an invalid or missing recipe, a `{version}` or
  `{commit}` with no value, a `.gitignore` with a `!` line): `PROBE: refused: <reason>`,
  exit 2, and no command runs.
- A command that is not one of the recipe's `health`, `version_probe` or `prod_smoke`
  entries (`--only rollback`, any other `--only` name, an extra argument after `probe`,
  or a probe entry whose program word holds a `{token}`):
  `PROBE: refused: <what> ... (S4)`, exit 2, and no command runs, not even the allowed
  lists (R23).
- One failed probe: no record. The second consecutive failure: one record with the probe
  output, the last release record and the recent default-branch CI runs, one `incident`
  line in the signals file, and `INCIDENT: opened <id> <path>`. A third failure opens no
  second record: `INCIDENT: open <id> <path> probes=3` (R24).

Planned (not yet shipped):

- The open record: the recipe's rollback argv word for word. The rollback is shown, never
  run (R25).
- `incident close --by <name>`: closes the record and prints
  `NEXT: run bug-autopsy with <record path>`. Without `--by` it exits 2 (R26).

## Drive it

On the checkout under test, make a fixture repo and probe it twice: once with every stub
passing, once with a failing health stub.

```sh
IP="$WT/.verify-run/$INSTANCE/ip"
python3 incident-watch/assets/incident_testkit.py fixture "$IP/pass"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-pass -- \
  python3 incident-watch/assets/incident.py --root "$IP/pass" probe
cp "$IP/pass/.skill-contract/incidents/probes.jsonl" "$WT/.verify-run/$INSTANCE/ip-pass-probes.jsonl"
cp "$IP/pass/stubs/calls.log" "$WT/.verify-run/$INSTANCE/ip-pass-calls.log"
ls -A "$IP/pass/.skill-contract/incidents" > "$WT/.verify-run/$INSTANCE/ip-pass-state.txt"
git -C "$IP/pass" check-ignore -v .skill-contract/incidents/probes.jsonl \
  > "$WT/.verify-run/$INSTANCE/ip-pass-ignored.txt"
python3 incident-watch/assets/incident_testkit.py fixture "$IP/fail" --fail health
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-fail -- \
  python3 incident-watch/assets/incident.py --root "$IP/fail" probe
cp "$IP/fail/stubs/calls.log" "$WT/.verify-run/$INSTANCE/ip-fail-calls.log"
python3 incident-watch/assets/incident_testkit.py fixture "$IP/refuse"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-refuse-only -- \
  python3 incident-watch/assets/incident.py --root "$IP/refuse" probe --only health --only rollback
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-refuse-argv -- \
  python3 incident-watch/assets/incident.py --root "$IP/refuse" probe rollback
ls -A "$IP/refuse/stubs" > "$WT/.verify-run/$INSTANCE/ip-refuse-stubs.txt"
python3 incident-watch/assets/incident_testkit.py fixture "$IP/inc" --fail health
for n in 1 2 3; do
  python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-inc-$n -- \
    python3 incident-watch/assets/incident.py --root "$IP/inc" probe
  ls -A "$IP/inc/.skill-contract/incidents/records" \
    > "$WT/.verify-run/$INSTANCE/ip-inc-$n-records.txt" 2>&1
done
cp "$IP/inc/.skill-contract/incidents/signals.jsonl" "$WT/.verify-run/$INSTANCE/ip-inc-signals.jsonl"
cp "$IP/inc/.skill-contract/incidents/records/"*.json "$WT/.verify-run/$INSTANCE/ip-inc-record.json"
cp "$IP/inc/stubs/calls.log" "$WT/.verify-run/$INSTANCE/ip-inc-calls.log"
```

- `ip-pass`: exit 0. The lines are `PROBE: health pass rc=0`,
  `PROBE: version_probe pass rc=0`, `PROBE: prod_smoke[0] pass rc=0`,
  `PROBE: prod_smoke[1] pass rc=0` and
  `PROBE_RESULT: pass commands=4 failed=0 consecutive_failures=0`.
- `ip-pass-probes.jsonl`: one line, `"result": "pass"`, with four commands, each with
  `at`, `argv`, `rc` 0 and `out_tail`.
- `ip-pass-state.txt`: only `.gitignore`, `.lock`, `probes.jsonl` and `state.json`. There
  is no incident record.
- `ip-pass-ignored.txt`: one line that names `.skill-contract/incidents/.gitignore`.
- `ip-pass-calls.log` and `ip-fail-calls.log`: only `health production`, `version_probe`,
  `prod_smoke_0` and `prod_smoke_1`. No `build`, `deploy_staging`, `deploy_prod`,
  `rollback` or `staging_check` line.
- `ip-fail`: exit 3, `PROBE: health fail rc=1` and
  `PROBE_RESULT: fail commands=4 failed=1 consecutive_failures=1`.

- `ip-refuse-only`: exit 2 and one line,
  `PROBE: refused: --only 'rollback' is not a probe list; a probe runs only health, version_probe, prod_smoke (S4)`.
- `ip-refuse-argv`: exit 2 and one line that starts
  `PROBE: refused: unexpected argument 'rollback' after probe`.
- `ip-refuse-stubs.txt`: only `stub.py`. There is no `calls.log`, so no stub ran, not
  even `health`.

- `ip-inc-1`: exit 3, `consecutive_failures=1` and no `INCIDENT:` line.
  `ip-inc-1-records.txt` says that the records directory does not exist.
- `ip-inc-2`: exit 3, `consecutive_failures=2`, `INCIDENT: opened <id> <path>`,
  `SIGNAL: .skill-contract/incidents/signals.jsonl kind incident`, the
  `NEXT: intake import ...` line and the `NEXT: gh run list ...` line (the fixture has
  no CI runs on disk). `ip-inc-2-records.txt` holds one `<id>.json`.
- `ip-inc-3`: exit 3, `INCIDENT: open <id> <path> probes=3` with the same id, and no
  `INCIDENT: opened` line. `ip-inc-3-records.txt` still holds one file.
- `ip-inc-signals.jsonl`: one line, `"kind": "incident"`, `"source": "incident-watch"`,
  `"source_id"` equal to the record id.
- `ip-inc-record.json`: `"status": "open"`, three `probes` whose `health` command has
  `stub health production exit 1` in its `out_tail`, and the `last_release` and
  `ci_runs` fields (each with `"available": false` in the fixture).
- `ip-inc-calls.log`: only probe stubs. No `rollback` line.

The R25 and R26 recipes join this section as each ships.

## Proof

The `ip-pass` and `ip-fail` captures (their `PROBE:` and `PROBE_RESULT:` lines and exit
codes), `ip-pass-probes.jsonl`, `ip-pass-state.txt`, `ip-pass-ignored.txt` and both
`calls.log` copies. The `ip-refuse-only` and `ip-refuse-argv` captures (exit 2 and the
`PROBE: refused:` line) and `ip-refuse-stubs.txt` with no `calls.log` (R23). The `ip-inc-1` to `ip-inc-3` captures,
their records listings, `ip-inc-signals.jsonl`, `ip-inc-record.json` and `ip-inc-calls.log`
(R24). After the planned parts ship: the record's rollback argv, the `NEXT:` line from
`incident close`, and a rollback stub that was never called.

## Gotchas

- Without `--skills-dir`, the tool finds release-conductor beside incident-watch first.
  If release-conductor is not installed, the probe refuses with exit 2.
- Never point a probe at production while driving a fixture: the probe runs only
  read-only commands, but a wrong recipe is still a real command.
- The output tails in `probes.jsonl` can carry production details. They stay in the
  git-ignored state directory. Do not paste them into a public place.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
