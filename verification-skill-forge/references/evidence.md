# Evidence records and the conductor's check

## Layout

```text
.verify/
  <instance>/
    doctor/<sha>/doctor.json
    <feature-id>/<sha>/evidence.json
    <feature-id>/<sha>/<artifacts…>
  findings/<feature-id>-<sha12>.json
.verify-run/            # per-instance run state; cleanup may delete it
  ports/<port>.lock
  <instance>/…
```

`<sha>` is the 40-hex HEAD of the worktree the verifier drove, read by the recorder from
git.

## evidence.json (`verify-evidence/v1`)

```json
{"schema": "verify-evidence/v1", "kind": "evidence", "instance": "a",
 "feature": "notes-create", "sha": "<40 hex>", "verifier": "<agent id>",
 "result": "pass", "action": "POST /notes title=groceries",
 "observed": "201 with id; GET /notes lists it",
 "side_effects": ["a row in notes.jsonl"],
 "artifacts": [{"path": "create.json", "sha256": "<64 hex>"}],
 "captured_at": "2026-09-29T10:00:00Z"}
```

## Proof records (`record --tier`)

```bash
python3 verify_evidence.py record --instance a --feature notes-create --verifier "$VERIFIER" \
  --tier proved --tool verus --clauses C1 --cmd "verus src/notes.rs" [--timeout 900]
```

The recorder runs the command itself. It splits `--cmd` with shlex, uses no shell, and
runs from the worktree root with a timeout. It writes a record only when the command
exits 0 and HEAD did not move. Otherwise it exits 1 and writes nothing. It refuses
`--result`, `--action`, `--observed`, `--side-effect` and `--artifact` with `--tier`,
because the tool's own output is the proof. All checks run before the first write.

Each tier accepts only the tools in its allowlist, `TIER_TOOLS` in `verify_evidence.py`:

| Tier | Allowed `--tool` |
|---|---|
| `proved` | `verus` |
| `bounded` | `kani` |
| `property` | `proptest`, `hypothesis`, `fast-check`, `rapid` |

CrossHair is not on the `bounded` list, because its bound is a time budget and not an
input bound. A record names exactly one clause in `--clauses`. The recorder refuses
another tool, a list such as `C1,C2`, or an empty value. It exits 2, does not run the
tool and writes nothing.

The record is `<feature-id>/<sha>/proof-<tier>-<tool>-<clause>.json`. It has the
evidence.json fields and artifact shape, so the conductor's artifact check applies to
it. It adds `tier`, `tool`, `clauses`, `argv`, `cwd`, `timeout` and `exit_status`. The
artifacts are the pinned `proof-<tier>-<tool>-<clause>.stdout` and `.stderr` files.

A `bounded` or `property` record also needs `--mutant-patch` and `--mutant-author`. The
recorder runs the same command on a mutated copy of HEAD and writes the record only when
that run fails with the tool's failure signature. The record then adds `mutant_author`,
`mutant_exit_status` and the pinned `.mutant.patch`, `.mutant.exit`, `.mutant.stdout` and
`.mutant.stderr` artifacts. The steps and the signatures are in `proof-tiers.md`.

```json
{"schema": "verify-evidence/v1", "kind": "evidence", "instance": "a",
 "feature": "notes-create", "sha": "<40 hex>", "verifier": "<agent id>",
 "result": "pass", "tier": "proved", "tool": "verus", "clauses": ["C1"],
 "argv": ["verus", "src/notes.rs"], "cwd": "<worktree root>", "timeout": 900,
 "exit_status": 0,
 "artifacts": [{"path": "proof-proved-verus-C1.stdout", "sha256": "<64 hex>"},
               {"path": "proof-proved-verus-C1.stderr", "sha256": "<64 hex>"}],
 "captured_at": "…"}
```

## doctor.json

```json
{"schema": "verify-evidence/v1", "kind": "doctor", "instance": "a", "sha": "<40 hex>",
 "ok": true, "checks": {"process": "pass", "port": "pass"}, "verifier": "<agent id>",
 "captured_at": "…"}
```

## How factory-conductor judges a head

When the plan declares a `verification` block, `conductor evidence <task> --verifier <id>`
passes a task's verified head only when all of these hold (the full list is in
factory-conductor's `references/run-protocol.md`):

1. The grant covers `local_reversible` now.
2. The verifier is not the owner recorded at `start`.
3. Every touched feature (the task's declared `features`, plus every feature whose anchor
   the diff touches) has a map entry at that head.
4. For each touched feature, `.verify/<instance>/<feature>/<head>/evidence.json` exists,
   its `sha` equals the head and its directory name, its `verifier` is this verifier, its
   `result` is `pass`, and every artifact exists with its recorded sha256.
5. The instance that produced it has a `doctor.json` for the same head with `ok: true`.

`merge` repeats checks 4–5 on the files as they are then, and re-checks the grant.
