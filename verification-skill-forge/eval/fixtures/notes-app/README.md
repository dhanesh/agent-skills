# notes

A tiny HTTP notes service: `python3 app.py --port 8080 --data ./data`.

- `GET /health` reports the version, instance and port.
- `POST /notes` with `{"title": "..."}` stores a note and returns it (201).
- `GET /notes` lists the stored notes.
