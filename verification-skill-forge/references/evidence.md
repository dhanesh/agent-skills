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
