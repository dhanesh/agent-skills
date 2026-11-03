# intake-queue: import ops signals into a ranked queue and pick one for planning

- id: intake-queue
- proven: no
- anchors: ops-intake/assets/intake.py:cmd_pick, ops-intake/assets/intake.py:cmd_sync, ops-intake/assets/fixtures/signals_ok.jsonl

## What it is

ops-intake keeps a local queue of operational signals. A person writes a config, imports
signal files into the queue, reads the queue, picks one item for planning and later marks
it resolved. `intake.py` runs no command and opens no socket. It only reads the files it
gets and writes its own queue under `<root>/.skill-contract/intake/`.

## How to reach it

`python3 ops-intake/assets/intake.py --root <repo> <command>`. The commands are `init`,
`import`, `status`, `list`, `show`, `pick`, `sync`, `resolve`, `dismiss`, `link`,
`drop-run` and `formats`. The machine lines are `IMPORT:`, `SYNC:`, `ITEM:`, `NEXT:`,
`STOP:` and `PROBLEM:`.

## Drive it

Build an empty project root and a config in the instance directory. Then run each command
through `capture`:

```sh
IQ="$WT/.verify-run/$INSTANCE/iq"
mkdir -p "$IQ"
printf '%s\n' '{"sources": {"jira": {"enabled": true, "formats": ["intake-signals-jsonl"]}}}' \
  > "$WT/.verify-run/$INSTANCE/iq-answers.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-init -- \
  python3 ops-intake/assets/intake.py --root "$IQ" init --answers "$WT/.verify-run/$INSTANCE/iq-answers.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-import -- \
  python3 ops-intake/assets/intake.py --root "$IQ" import --format intake-signals-jsonl \
  --source jira ops-intake/assets/fixtures/signals_ok.jsonl
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-status -- \
  python3 ops-intake/assets/intake.py --root "$IQ" status
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-list -- \
  python3 ops-intake/assets/intake.py --root "$IQ" list
```

Expect exit 0 from all four:

- `init` prints `NEXT: config written to .intake/config.json`.
- `import` prints `IMPORT: jira intake-signals-jsonl ok 2 problems 0`. Run it again: the
  line is the same and the queue still holds 2 items (the importer dedupes).
- `status` prints
  `SYNC: 2 items new=2 picked=0 planned=0 resolved=0 dismissed=0 regressed=0`.
- `list` prints two `ITEM:` lines: `ITEM: I84203d4a32 new+low-trust 0 `db slow``
  and `ITEM: I8ba019757d new+low-trust 0 `flaky``. The item id is `I` and the first 10
  hex digits of sha256(`<source>\0<source_id>`), so it is the same on every run.

Then pick, sync and resolve:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-pick -- \
  python3 ops-intake/assets/intake.py --root "$IQ" pick I84203d4a32 --by verifier
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-repick -- \
  python3 ops-intake/assets/intake.py --root "$IQ" pick I84203d4a32 --by verifier
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-sync -- \
  python3 ops-intake/assets/intake.py --root "$IQ" sync
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name iq-resolve -- \
  python3 ops-intake/assets/intake.py --root "$IQ" resolve I84203d4a32 --by verifier
```

- `pick` exits 0 and prints `ITEM: I84203d4a32 picked+low-trust 0 `db slow``, then
  `NEXT: run spec-first-planning with .skill-contract/intake/envelopes/intake-item-v1-<time>-<hex>.json`.
- The second `pick` exits 2 and prints
  `STOP: only a new item can be picked; `I84203d4a32` is picked+low-trust`.
- `sync` exits 0 and prints
  `SYNC: 2 items new=1 picked=1 planned=0 resolved=0 dismissed=0 envelopes 0 problems 0`.
  `sync` also stores each item's rank.
- `resolve` exits 0 and prints `ITEM: I84203d4a32 resolved+low-trust 301 `db slow``.
  The rank 301 is severity 3 times 100, plus 0 recency points (the fixture's dates are
  old), plus a count of 1.

Refusals: `import --format nope x` exits 2 with `STOP: unknown-format `nope``. Any
command on a root with no config exits 2 with `STOP: config: no config at .intake/config.json`.

## Proof

The captures' result lines and exit codes, and the files the commands wrote under `$IQ`:
`.intake/config.json`, `.skill-contract/intake/queue.json`,
`.skill-contract/intake/intake-log.jsonl`, `.skill-contract/intake/items/I84203d4a32.json`
and one `intake-item-v1-*.json` envelope under `.skill-contract/intake/envelopes/`. List
them with `find "$IQ" -type f` and give the list to `record` as an extra `--artifact`.
The refused second `pick` writes no second envelope.

## Gotchas

- `intake-signals-jsonl` is the lower-trust format, so every item from it carries
  `+low-trust` in its flag.
- The rank is 0 until the first `sync` stores it.
- `--root` must come before the command name.
- The envelope name holds the UTC time, so do not compare it byte for byte across runs.
