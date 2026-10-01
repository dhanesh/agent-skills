# notes-create: store a note

- id: notes-create
- proven: no
- anchors: app.py:do_POST
- constraints: notes:B1

## What it is

A client sends a title and gets back the stored note with its id.

## How to reach it

`POST /notes` with a JSON body `{"title": "..."}`.

## Drive it

```sh
python3 "$SKILL/scripts/notes_harness.py" create --instance "$INSTANCE" --title "groceries" --capture ".verify-run/$INSTANCE/capture"
```

Expect exit 0 and JSON whose `response.status` is 201 and whose `rows` holds the title.

## Proof

The response is 201 with an `id`, and `.verify-run/$INSTANCE/data/notes.jsonl` gained a
row with the same title (the side effect, not just the response).

## Gotchas

An empty or missing title is a 400, not a stored note.
