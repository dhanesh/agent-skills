# notes-list: read back notes

- id: notes-list
- proven: no
- anchors: app.py:do_GET

## What it is

A client lists every note stored on this instance.

## How to reach it

`GET /notes`.

## Drive it

```sh
python3 "$SKILL/scripts/notes_harness.py" list --instance "$INSTANCE"
```

Expect exit 0 and JSON whose `body.notes` lists each stored title.

## Proof

A title created on this instance appears in the list; a title created on another instance
does not.

## Gotchas

Notes are per instance: each instance has its own data store.
