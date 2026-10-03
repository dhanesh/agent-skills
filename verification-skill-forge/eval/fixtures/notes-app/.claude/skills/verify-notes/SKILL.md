---
name: verify-notes
description: >-
  Drive the notes HTTP service (app.py) the way a client does and capture proof that a
  change works: launch an isolated instance, check it, create and list notes over HTTP,
  record evidence. Use when a change touches app.py or the notes API.
---

# verify-notes

`notes` is a small HTTP service (`app.py`): a client creates notes with `POST /notes` and
reads them with `GET /notes`. This skill drives it over HTTP with
`scripts/notes_harness.py`, which prints JSON. Run every command from the app root, and
set once per session:

```sh
SKILL=.claude/skills/verify-notes
INSTANCE=a          # one name per owner; two owners never share one
VERIFIER=<your agent id>
```

## Launch

Each instance gets its own port (claimed through `scripts/verify_evidence.py port`, so two
owners never get the same one), its own data store (`.verify-run/$INSTANCE/data`) and its
own process. The service needs no login, so there is no auth session to isolate. Launch
waits for `/health` to answer for this instance and gives up after 10 seconds; it is
usually ready in under 1 second.

```sh
python3 "$SKILL/scripts/verify_evidence.py" port --instance "$INSTANCE"
python3 "$SKILL/scripts/notes_harness.py" launch --instance "$INSTANCE"
```

`launch` prints `{"ready": true, "port": ...}` and exits 0, or `"ready": false` and exits 1.

## Doctor

Read-only: checks that the process this instance started is alive, that `/health` on the
instance's port answers for this instance, and that the version is the one this skill
knows. `--record` writes the result through `scripts/verify_evidence.py doctor` for the
current head. Run it first, and again after anything surprising.

```sh
python3 "$SKILL/scripts/notes_harness.py" doctor --instance "$INSTANCE" --record --verifier "$VERIFIER"
```

## Drive

The harness calls the real HTTP routes, `POST /notes` and `GET /notes`: the same path a
client takes. Feature recipes are in `features/`.

```sh
python3 "$SKILL/scripts/notes_harness.py" create --instance "$INSTANCE" --title "groceries" --capture ".verify-run/$INSTANCE/capture"
python3 "$SKILL/scripts/notes_harness.py" list --instance "$INSTANCE"
```

## Evidence

- Exercise the real user path. Never internal setters, never test-only endpoints.
- Capture the action *and* the resulting state, not just a final screenshot.
- Verify side effects (rows inserted, files written, messages sent, webhooks fired) alongside what is visible.
- Mocks only where a production boundary already isolates the external system.
- Where the safe path is a dry-run or test mode, verify what it *actually* skips by observing files, network and git refs, not by trusting its name. Some dry-runs still touch the network or open a browser.
- Evidence goes to `.verify/<instance>/<feature-id>/<sha>/`, one directory per feature per head, recorded with `scripts/verify_evidence.py record`.
- Harness output is JSON wherever the harness can emit it, so a verifier can read it without a human.
- Cleanup removes instances, never evidence.

```sh
python3 "$SKILL/scripts/verify_evidence.py" record --instance "$INSTANCE" --feature notes-create --verifier "$VERIFIER" --result pass --action "POST /notes title=groceries" --observed "201 with id; GET /notes lists it" --side-effect "a row in .verify-run/$INSTANCE/data/notes.jsonl" --artifact ".verify-run/$INSTANCE/capture/create.json"
```

## Cleanup

Stops the process group this instance started (by its pid file, never by name), releases
its port and removes its run state. Evidence under `.verify/` stays.

```sh
python3 "$SKILL/scripts/notes_harness.py" stop --instance "$INSTANCE"
python3 "$SKILL/scripts/verify_evidence.py" release --instance "$INSTANCE"
rm -rf ".verify-run/$INSTANCE"
```
